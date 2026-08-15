"""Manual-payment adapter; application state remains authoritative in PostgreSQL."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256

from app.domain.enums import PaymentMode, PaymentStatus, RefundStatus
from app.payments.providers.base import (
    PaymentCancelCommand,
    PaymentCreateCommand,
    PaymentProviderOperationUnsupported,
    PaymentRefundCommand,
    ProviderPayment,
    ProviderRefund,
    ProviderWebhookEvent,
)


class ManualPaymentProvider:
    """Create deterministic manual intents without accepting card data."""

    mode = PaymentMode.MANUAL
    supports_partial_refunds = True

    async def create_payment(self, command: PaymentCreateCommand) -> ProviderPayment:
        identifier = self._identifier("manual_payment", command.idempotency_key)
        return ProviderPayment(
            provider=self.mode,
            provider_payment_id=identifier,
            status=PaymentStatus.PENDING,
            amount=command.amount,
            currency=command.currency,
            safe_metadata=command.safe_metadata,
        )

    async def get_payment(self, provider_payment_id: str) -> ProviderPayment:
        raise PaymentProviderOperationUnsupported("manual_status_requires_database_confirmation")

    async def cancel_payment(self, command: PaymentCancelCommand) -> ProviderPayment:
        return ProviderPayment(
            provider=self.mode,
            provider_payment_id=command.provider_payment_id,
            status=PaymentStatus.CANCELLED,
            amount=command.amount,
            currency=command.currency,
            safe_metadata=command.safe_metadata,
        )

    async def refund_payment(self, command: PaymentRefundCommand) -> ProviderRefund:
        return ProviderRefund(
            provider=self.mode,
            provider_refund_id=self._identifier("manual_refund", command.idempotency_key),
            provider_payment_id=command.provider_payment_id,
            status=RefundStatus.PENDING,
            amount=command.amount,
            currency=command.currency,
        )

    async def get_refund(self, provider_refund_id: str) -> ProviderRefund:
        raise PaymentProviderOperationUnsupported("manual_refund_requires_database_confirmation")

    def parse_webhook(self, payload: Mapping[str, object]) -> ProviderWebhookEvent:
        raise PaymentProviderOperationUnsupported("manual_mode_has_no_webhook")

    @staticmethod
    def _identifier(prefix: str, idempotency_key: str) -> str:
        digest = sha256(idempotency_key.encode()).hexdigest()[:32]
        return f"{prefix}_{digest}"
