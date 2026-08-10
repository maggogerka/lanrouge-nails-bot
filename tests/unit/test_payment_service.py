"""Payment service verification, refund accounting and webhook replay tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.database.models.payment import Payment
from app.domain.enums import PaymentMode, PaymentStatus, RefundStatus
from app.domain.payments import PaymentStateError, WebhookProcessingStatus
from app.payments.providers import MockPaymentProvider
from app.payments.providers.base import PaymentProviderError, ProviderPayment
from app.schemas.payment import PaymentCreate, RefundCreate
from app.services.payment_service import PaymentService

NOW = datetime(2026, 8, 10, 12, tzinfo=UTC)
KEY = "12345678-1234-1234-1234-123456789012"


def payment_values(**overrides: object) -> PaymentCreate:
    values: dict[str, object] = {
        "business_id": 7,
        "appointment_id": 11,
        "provider": PaymentMode.YOOKASSA,
        "payment_type": "deposit",
        "amount": Decimal("500.00"),
        "currency": "RUB",
        "idempotency_key": KEY,
        "safe_metadata": {"campaign": "summer"},
        "return_url": "https://example.test/payment-return",
    }
    values.update(overrides)
    return PaymentCreate.model_validate(values)


async def created_payment() -> tuple[PaymentService, MockPaymentProvider, Payment]:
    provider = MockPaymentProvider()
    service = PaymentService(provider)
    values = payment_values()
    payment = service.new_payment(values, expires_at=NOW + timedelta(minutes=20))
    payment.id = 31
    await service.create_with_provider(payment, values, now=NOW)
    return service, provider, payment


@pytest.mark.asyncio
async def test_create_and_authoritative_refresh_verify_payment() -> None:
    service, provider, raw_payment = await created_payment()
    payment = raw_payment

    assert payment.status is PaymentStatus.PENDING
    assert payment.safe_metadata == {
        "campaign": "summer",
        "business_id": "7",
        "appointment_id": "11",
    }
    assert payment.provider_payment_id is not None
    provider.set_payment_status(payment.provider_payment_id, PaymentStatus.SUCCEEDED)

    await service.refresh_from_provider(payment, now=NOW + timedelta(minutes=1))

    assert payment.status is PaymentStatus.SUCCEEDED
    assert payment.paid_at == NOW + timedelta(minutes=1)


@pytest.mark.asyncio
async def test_provider_money_mismatch_never_confirms_payment() -> None:
    service, _, raw_payment = await created_payment()
    payment = raw_payment
    malicious = ProviderPayment(
        provider=PaymentMode.YOOKASSA,
        provider_payment_id=payment.provider_payment_id,
        status=PaymentStatus.SUCCEEDED,
        amount=Decimal("499.00"),
        currency="RUB",
        safe_metadata=payment.safe_metadata,
    )

    with pytest.raises(PaymentProviderError, match="money_mismatch"):
        service.apply_authoritative_payment(payment, malicious, now=NOW)

    assert payment.status is PaymentStatus.PENDING
    assert payment.paid_at is None


def test_idempotency_key_cannot_be_reused_for_another_appointment() -> None:
    provider = MockPaymentProvider()
    service = PaymentService(provider)
    original = payment_values()
    payment = service.new_payment(original, expires_at=NOW + timedelta(minutes=20))

    with pytest.raises(PaymentStateError, match="идемпотентности"):
        service.require_same_intent(payment, payment_values(appointment_id=12))


@pytest.mark.asyncio
async def test_partial_then_full_refund_is_exact_and_replay_safe() -> None:
    service, provider, raw_payment = await created_payment()
    payment = raw_payment
    assert payment.provider_payment_id is not None
    provider.set_payment_status(payment.provider_payment_id, PaymentStatus.SUCCEEDED)
    await service.refresh_from_provider(payment, now=NOW)

    first = service.new_refund(
        payment,
        RefundCreate(
            business_id=7,
            payment_id=31,
            amount=Decimal("100.00"),
            idempotency_key="refund-1234567890123456",
        ),
        committed_or_pending_amount=Decimal("0.00"),
    )
    first_result = await service.refund_with_provider(payment, first, now=NOW)

    assert first.status is RefundStatus.SUCCEEDED
    assert payment.status is PaymentStatus.PARTIALLY_REFUNDED
    assert payment.refunded_amount == Decimal("100.00")
    service.apply_authoritative_refund(payment, first, first_result, now=NOW)
    assert payment.refunded_amount == Decimal("100.00")

    second = service.new_refund(
        payment,
        RefundCreate(
            business_id=7,
            payment_id=31,
            amount=Decimal("400.00"),
            idempotency_key="refund-9876543210987654",
        ),
        committed_or_pending_amount=Decimal("100.00"),
    )
    await service.refund_with_provider(payment, second, now=NOW + timedelta(minutes=1))

    assert payment.status is PaymentStatus.REFUNDED
    assert payment.refunded_amount == payment.amount
    assert payment.refunded_at == NOW + timedelta(minutes=1)


@pytest.mark.asyncio
async def test_over_refund_is_rejected_before_provider_call() -> None:
    service, provider, raw_payment = await created_payment()
    payment = raw_payment
    assert payment.provider_payment_id is not None
    provider.set_payment_status(payment.provider_payment_id, PaymentStatus.SUCCEEDED)
    await service.refresh_from_provider(payment, now=NOW)

    with pytest.raises(PaymentStateError, match="превышает остаток"):
        service.new_refund(
            payment,
            RefundCreate(
                business_id=7,
                payment_id=31,
                amount=Decimal("500.00"),
                idempotency_key="refund-1234567890123456",
            ),
            committed_or_pending_amount=Decimal("1.00"),
        )


@pytest.mark.asyncio
async def test_webhook_event_is_bounded_and_authoritatively_refetched() -> None:
    service, provider, raw_payment = await created_payment()
    payment = raw_payment
    assert payment.provider_payment_id is not None
    provider.set_payment_status(payment.provider_payment_id, PaymentStatus.SUCCEEDED)
    parsed = provider.parse_webhook(
        {"event": "payment.succeeded", "provider_payment_id": payment.provider_payment_id}
    )
    event = service.new_webhook_event(
        business_id=7,
        event=parsed,
        received_at=NOW,
        retention_days=14,
        correlation_id="request-1",
    )

    await service.process_payment_webhook(payment, event, now=NOW + timedelta(seconds=1))

    assert payment.status is PaymentStatus.SUCCEEDED
    assert event.status is WebhookProcessingStatus.PROCESSED
    assert event.payment_id == payment.id
    assert event.expires_at == NOW + timedelta(days=14)
    assert event.payload_sha256 == event.event_key

    with pytest.raises(PaymentStateError, match="уже обработан"):
        await service.process_payment_webhook(payment, event, now=NOW + timedelta(seconds=2))


@pytest.mark.asyncio
async def test_cross_business_webhook_is_rejected_before_provider_lookup() -> None:
    service, provider, raw_payment = await created_payment()
    payment = raw_payment
    assert payment.provider_payment_id is not None
    parsed = provider.parse_webhook(
        {"event": "payment.succeeded", "provider_payment_id": payment.provider_payment_id}
    )
    event = service.new_webhook_event(business_id=8, event=parsed, received_at=NOW)

    with pytest.raises(PaymentStateError, match="другому бизнесу"):
        await service.process_payment_webhook(payment, event, now=NOW)


@pytest.mark.asyncio
async def test_manual_confirmation_is_explicit_and_provider_scoped() -> None:
    from app.payments.providers import ManualPaymentProvider

    provider = ManualPaymentProvider()
    service = PaymentService(provider)
    values = payment_values(provider=PaymentMode.MANUAL, return_url=None)
    payment = service.new_payment(values, expires_at=NOW + timedelta(minutes=20))
    payment.id = 44
    await service.create_with_provider(payment, values, now=NOW)

    service.confirm_manual_payment(payment, now=NOW + timedelta(minutes=2))

    assert payment.status is PaymentStatus.SUCCEEDED
    assert payment.paid_at == NOW + timedelta(minutes=2)
