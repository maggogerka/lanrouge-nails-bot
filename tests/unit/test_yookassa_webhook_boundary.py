"""YooKassa boundary tests: bounded parsing followed by authoritative processing."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import SecretStr

from app.api.contracts import SafeHttpError
from app.api.webhooks import (
    WebhookDisposition,
    WebhookProcessingError,
    YooKassaWebhookBoundary,
)
from app.payments.providers.base import HttpRequest, HttpResponse
from app.payments.providers.yookassa import YooKassaPaymentProvider


class UnusedTransport:
    async def request(self, request: HttpRequest) -> HttpResponse:
        raise AssertionError("webhook boundary must not call transport directly")


def boundary() -> tuple[YooKassaWebhookBoundary, MagicMock]:
    parser = YooKassaPaymentProvider(
        UnusedTransport(),
        shop_id="shop-7",
        secret_key=SecretStr("provider-secret"),
    )
    processor = MagicMock()
    processor.process_untrusted_notification = AsyncMock(
        return_value=WebhookDisposition(duplicate=False)
    )
    return YooKassaWebhookBoundary(parser, processor), processor


@pytest.mark.asyncio
async def test_notification_status_and_amount_are_not_trusted_by_boundary() -> None:
    target, processor = boundary()
    payload = {
        "type": "notification",
        "event": "payment.succeeded",
        "object": {
            "id": "payment_123",
            "status": "succeeded",
            "amount": {"value": "0.01", "currency": "XXX"},
        },
    }

    result = await target.handle(
        json.dumps(payload).encode(),
        content_type="application/json; charset=utf-8",
        correlation_id="request-12345678",
    )

    assert not result.duplicate
    event = processor.process_untrusted_notification.await_args.args[0]
    assert event.provider_payment_id == "payment_123"
    assert not hasattr(event, "status")
    assert not hasattr(event, "amount")
    processor.process_untrusted_notification.assert_awaited_once_with(
        event,
        correlation_id="request-12345678",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b"[]",
        b'{"event":"payment.succeeded","event":"payment.canceled"}',
    ],
)
async def test_invalid_or_ambiguous_json_is_rejected(body: bytes) -> None:
    target, processor = boundary()

    with pytest.raises(SafeHttpError) as error:
        await target.handle(
            body,
            content_type="application/json",
            correlation_id="request-12345678",
        )

    assert error.value.status_code == 400
    processor.process_untrusted_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_unsupported_event_and_content_type_are_generic() -> None:
    target, processor = boundary()
    body = json.dumps({"event": "unknown", "object": {"id": "payment_123"}}).encode()

    with pytest.raises(SafeHttpError) as event_error:
        await target.handle(
            body,
            content_type="application/json",
            correlation_id="request-12345678",
        )
    assert event_error.value.code == "webhook_envelope_invalid"

    with pytest.raises(SafeHttpError) as type_error:
        await target.handle(
            body,
            content_type="text/plain",
            correlation_id="request-12345678",
        )
    assert type_error.value.status_code == 415
    processor.process_untrusted_notification.assert_not_awaited()


@pytest.mark.asyncio
async def test_retryable_processor_failure_returns_retryable_boundary_error() -> None:
    target, processor = boundary()
    processor.process_untrusted_notification.side_effect = WebhookProcessingError(retryable=True)
    body = json.dumps({"event": "payment.succeeded", "object": {"id": "payment_123"}}).encode()

    with pytest.raises(SafeHttpError) as error:
        await target.handle(
            body,
            content_type="application/json",
            correlation_id="request-12345678",
        )

    assert error.value.status_code == 503
    assert error.value.headers == {"retry-after": "5"}


@pytest.mark.asyncio
async def test_boundary_enforces_size_even_without_asgi_wrapper() -> None:
    target, processor = boundary()

    with pytest.raises(SafeHttpError) as error:
        await target.handle(
            b" " * 65_537,
            content_type="application/json",
            correlation_id="request-12345678",
        )

    assert error.value.status_code == 413
    processor.process_untrusted_notification.assert_not_awaited()
