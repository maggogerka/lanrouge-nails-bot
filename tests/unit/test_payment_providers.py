"""Provider contract tests, including safe YooKassa transport behavior."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from decimal import Decimal

import pytest
from pydantic import SecretStr

from app.domain.enums import PaymentStatus, RefundStatus
from app.domain.payments import PaymentType
from app.payments.providers import (
    HttpRequest,
    HttpResponse,
    ManualPaymentProvider,
    MockPaymentProvider,
    PaymentCancelCommand,
    PaymentCreateCommand,
    PaymentProviderError,
    PaymentRefundCommand,
    YooKassaPaymentProvider,
)
from app.payments.providers.base import PaymentProviderOperationUnsupported


class QueueTransport:
    def __init__(self, *responses: HttpResponse) -> None:
        self.responses = list(responses)
        self.requests: list[HttpRequest] = []

    async def request(self, request: HttpRequest) -> HttpResponse:
        self.requests.append(request)
        return self.responses.pop(0)


def create_command() -> PaymentCreateCommand:
    return PaymentCreateCommand(
        business_id=7,
        appointment_id=11,
        idempotency_key="12345678-1234-1234-1234-123456789012",
        amount=Decimal("500.00"),
        currency="RUB",
        payment_type=PaymentType.DEPOSIT,
        safe_metadata={"business_id": "7", "appointment_id": "11"},
        return_url="https://example.test/payment-return",
        description="Предоплата услуги",
    )


@pytest.mark.asyncio
async def test_manual_provider_is_deterministic_and_never_handles_card_data() -> None:
    provider = ManualPaymentProvider()
    first = await provider.create_payment(create_command())
    replay = await provider.create_payment(create_command())

    assert first == replay
    assert first.status is PaymentStatus.PENDING
    with pytest.raises(PaymentProviderOperationUnsupported):
        await provider.get_payment(first.provider_payment_id)


@pytest.mark.asyncio
async def test_manual_cancel_and_refund_are_explicit_pending_workflows() -> None:
    provider = ManualPaymentProvider()
    payment = await provider.create_payment(create_command())
    cancelled = await provider.cancel_payment(
        PaymentCancelCommand(
            provider_payment_id=payment.provider_payment_id,
            idempotency_key="cancel-1234567890123456",
            amount=payment.amount,
            currency=payment.currency,
            safe_metadata=payment.safe_metadata,
        )
    )
    refund = await provider.refund_payment(
        PaymentRefundCommand(
            provider_payment_id=payment.provider_payment_id,
            idempotency_key="refund-1234567890123456",
            amount=Decimal("100.00"),
            currency="RUB",
            reason_code="requested_by_business",
        )
    )

    assert cancelled.status is PaymentStatus.CANCELLED
    assert refund.status is RefundStatus.PENDING


@pytest.mark.asyncio
async def test_mock_provider_replays_create_and_refund_by_idempotency_key() -> None:
    provider = MockPaymentProvider()
    payment_one = await provider.create_payment(create_command())
    payment_two = await provider.create_payment(create_command())
    refund_command = PaymentRefundCommand(
        provider_payment_id=payment_one.provider_payment_id,
        idempotency_key="refund-1234567890123456",
        amount=Decimal("100.00"),
        currency="RUB",
        reason_code="requested_by_business",
    )
    refund_one = await provider.refund_payment(refund_command)
    refund_two = await provider.refund_payment(refund_command)

    assert payment_one == payment_two
    assert refund_one == refund_two
    assert await provider.get_refund(refund_one.provider_refund_id) == refund_one


@pytest.mark.asyncio
async def test_mock_provider_concurrent_refund_replay_is_idempotent() -> None:
    provider = MockPaymentProvider()
    payment = await provider.create_payment(create_command())
    command = PaymentRefundCommand(
        provider_payment_id=payment.provider_payment_id,
        idempotency_key="refund-concurrent-123456",
        amount=Decimal("100.00"),
        currency="RUB",
        reason_code="requested_by_business",
    )

    first, second = await asyncio.gather(
        provider.refund_payment(command),
        provider.refund_payment(command),
    )

    assert first == second
    assert first.provider_refund_id == second.provider_refund_id


@pytest.mark.asyncio
async def test_yookassa_request_repr_never_contains_secret_or_json_payload() -> None:
    transport = QueueTransport(
        HttpResponse(
            200,
            {
                "id": "payment_123",
                "status": "pending",
                "amount": {"value": "500.00", "currency": "RUB"},
                "metadata": {"business_id": "7", "appointment_id": "11"},
                "confirmation": {"confirmation_url": "https://yoomoney.example.test/confirmation"},
            },
        )
    )
    provider = YooKassaPaymentProvider(
        transport,
        shop_id="shop-7",
        secret_key=SecretStr("super-secret-key"),
    )

    result = await provider.create_payment(create_command())
    rendered = repr(transport.requests[0])

    assert result.status is PaymentStatus.PENDING
    assert "super-secret-key" not in rendered
    assert "Предоплата услуги" not in rendered
    assert "json_body" not in rendered
    assert transport.requests[0].url == "https://api.yookassa.ru/v3/payments"


@pytest.mark.asyncio
async def test_yookassa_rejects_money_malformed_response() -> None:
    transport = QueueTransport(
        HttpResponse(
            200,
            {
                "id": "payment_123",
                "status": "succeeded",
                "amount": {"value": "not-money", "currency": "RUB"},
            },
        )
    )
    provider = YooKassaPaymentProvider(transport, shop_id="shop-7", secret_key=SecretStr("secret"))

    with pytest.raises(PaymentProviderError) as error:
        await provider.get_payment("payment_123")

    assert error.value.code == "yookassa_amount_invalid"
    assert "not-money" not in str(error.value)


@pytest.mark.asyncio
async def test_yookassa_provider_id_is_validated_before_transport_call() -> None:
    transport = QueueTransport()
    provider = YooKassaPaymentProvider(transport, shop_id="shop-7", secret_key=SecretStr("secret"))

    with pytest.raises(PaymentProviderError, match="provider_object_id_invalid"):
        await provider.get_payment("../foreign-host")

    assert transport.requests == []


def test_yookassa_webhook_keeps_only_digest_and_requires_supported_shape() -> None:
    provider = YooKassaPaymentProvider(
        QueueTransport(), shop_id="shop-7", secret_key=SecretStr("secret")
    )
    payload: Mapping[str, object] = {
        "type": "notification",
        "event": "payment.succeeded",
        "object": {"id": "payment_123", "status": "succeeded"},
    }

    event = provider.parse_webhook(payload)

    assert event.provider_payment_id == "payment_123"
    assert event.event_key == event.payload_sha256
    assert len(event.payload_sha256) == 64
    with pytest.raises(PaymentProviderError, match="event_unsupported"):
        provider.parse_webhook({"event": "unknown", "object": {"id": "payment_123"}})


@pytest.mark.asyncio
async def test_yookassa_http_error_exposes_only_machine_code() -> None:
    transport = QueueTransport(HttpResponse(401, {"description": "secret provider response"}))
    provider = YooKassaPaymentProvider(transport, shop_id="shop-7", secret_key=SecretStr("secret"))

    with pytest.raises(PaymentProviderError) as error:
        await provider.get_payment("payment_123")

    assert str(error.value) == "yookassa_rejected_request"
    assert "provider response" not in str(error.value)
