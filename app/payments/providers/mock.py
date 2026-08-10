"""Deterministic in-memory provider for application and integration tests."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from hashlib import sha256

from app.domain.enums import PaymentMode, PaymentStatus, RefundStatus
from app.payments.providers.base import (
    PaymentCancelCommand,
    PaymentCreateCommand,
    PaymentProviderProtocolError,
    PaymentRefundCommand,
    ProviderPayment,
    ProviderRefund,
    ProviderWebhookEvent,
)


class MockPaymentProvider:
    """A stateful fake with provider-like idempotency and no external calls."""

    mode = PaymentMode.YOOKASSA
    supports_partial_refunds = True

    def __init__(self) -> None:
        self._payments: dict[str, ProviderPayment] = {}
        self._payment_keys: dict[str, str] = {}
        self._refunds: dict[str, ProviderRefund] = {}
        self._refund_keys: dict[str, str] = {}

    async def create_payment(self, command: PaymentCreateCommand) -> ProviderPayment:
        existing_id = self._payment_keys.get(command.idempotency_key)
        if existing_id is not None:
            return self._payments[existing_id]
        provider_id = self._identifier("mock_payment", command.idempotency_key)
        result = ProviderPayment(
            provider=self.mode,
            provider_payment_id=provider_id,
            status=PaymentStatus.PENDING,
            amount=command.amount,
            currency=command.currency,
            safe_metadata=dict(command.safe_metadata),
            confirmation_url=f"https://payments.example.test/{provider_id}",
        )
        self._payment_keys[command.idempotency_key] = provider_id
        self._payments[provider_id] = result
        return result

    async def get_payment(self, provider_payment_id: str) -> ProviderPayment:
        try:
            return self._payments[provider_payment_id]
        except KeyError as exc:
            raise PaymentProviderProtocolError("mock_payment_not_found") from exc

    async def cancel_payment(self, command: PaymentCancelCommand) -> ProviderPayment:
        payment = await self.get_payment(command.provider_payment_id)
        cancelled = replace(payment, status=PaymentStatus.CANCELLED)
        self._payments[command.provider_payment_id] = cancelled
        return cancelled

    async def refund_payment(self, command: PaymentRefundCommand) -> ProviderRefund:
        await self.get_payment(command.provider_payment_id)
        existing_id = self._refund_keys.get(command.idempotency_key)
        if existing_id is not None:
            return self._refunds[existing_id]
        provider_refund_id = self._identifier("mock_refund", command.idempotency_key)
        result = ProviderRefund(
            provider=self.mode,
            provider_refund_id=provider_refund_id,
            provider_payment_id=command.provider_payment_id,
            status=RefundStatus.SUCCEEDED,
            amount=command.amount,
            currency=command.currency,
        )
        self._refund_keys[command.idempotency_key] = provider_refund_id
        self._refunds[provider_refund_id] = result
        return result

    async def get_refund(self, provider_refund_id: str) -> ProviderRefund:
        try:
            return self._refunds[provider_refund_id]
        except KeyError as exc:
            raise PaymentProviderProtocolError("mock_refund_not_found") from exc

    def parse_webhook(self, payload: Mapping[str, object]) -> ProviderWebhookEvent:
        event_type = payload.get("event")
        provider_payment_id = payload.get("provider_payment_id")
        if not isinstance(event_type, str) or not isinstance(provider_payment_id, str):
            raise PaymentProviderProtocolError("mock_webhook_invalid")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        digest = sha256(canonical).hexdigest()
        return ProviderWebhookEvent(
            provider=self.mode,
            event_key=digest,
            event_type=event_type[:64],
            provider_object_id=provider_payment_id[:128],
            provider_payment_id=provider_payment_id[:128],
            payload_sha256=digest,
        )

    def set_payment_status(self, provider_payment_id: str, status: PaymentStatus) -> None:
        """Test hook representing an authoritative provider-side status change."""

        payment = self._payments[provider_payment_id]
        self._payments[provider_payment_id] = replace(payment, status=status)

    @staticmethod
    def _identifier(prefix: str, idempotency_key: str) -> str:
        return f"{prefix}_{sha256(idempotency_key.encode()).hexdigest()[:32]}"
