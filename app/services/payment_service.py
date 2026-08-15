"""Provider-independent payment lifecycle orchestration without persistence coupling."""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from app.database.models.payment import Payment, PaymentWebhookEvent, Refund
from app.domain.enums import PaymentMode, PaymentStatus, RefundStatus
from app.domain.payments import (
    PaymentStateError,
    WebhookProcessingStatus,
    aware_utc,
    require_payment_transition,
    require_refund_transition,
    validate_money,
    validate_safe_metadata,
)
from app.payments.providers.base import (
    PaymentCancelCommand,
    PaymentCreateCommand,
    PaymentProvider,
    PaymentProviderError,
    PaymentProviderProtocolError,
    PaymentRefundCommand,
    ProviderPayment,
    ProviderRefund,
    ProviderWebhookEvent,
)
from app.schemas.payment import PaymentCreate, RefundCreate


class PaymentService:
    """Mutate locked ORM aggregates while leaving transactions to the caller."""

    def __init__(self, provider: PaymentProvider) -> None:
        if provider.mode is PaymentMode.DISABLED:
            raise ValueError("disabled payment mode cannot be a provider")
        self._provider = provider

    @property
    def provider_mode(self) -> PaymentMode:
        return self._provider.mode

    def new_payment(
        self,
        values: PaymentCreate,
        *,
        expires_at: datetime | None,
    ) -> Payment:
        """Build a local intent; persist it before invoking an external provider."""

        self._require_provider(values.provider)
        expiry = aware_utc(expires_at) if expires_at is not None else None
        return Payment(
            business_id=values.business_id,
            appointment_id=values.appointment_id,
            provider=values.provider,
            provider_payment_id=None,
            idempotency_key=values.idempotency_key,
            amount=values.amount,
            refunded_amount=Decimal("0.00"),
            currency=values.currency,
            status=PaymentStatus.CREATED,
            payment_type=values.payment_type,
            safe_metadata=self._intent_metadata(values),
            confirmation_url=None,
            expires_at=expiry,
            attempts=0,
            correlation_id=values.correlation_id,
        )

    def require_same_intent(self, payment: Payment, values: PaymentCreate) -> None:
        """Reject accidental reuse of an idempotency key for different immutable data."""

        expected = (
            values.business_id,
            values.appointment_id,
            values.provider,
            values.payment_type,
            values.amount,
            values.currency,
            self._intent_metadata(values),
        )
        actual = (
            payment.business_id,
            payment.appointment_id,
            payment.provider,
            payment.payment_type,
            payment.amount,
            payment.currency,
            payment.safe_metadata,
        )
        if actual != expected or payment.idempotency_key != values.idempotency_key:
            raise PaymentStateError("Ключ идемпотентности уже связан с другим платежом.")

    async def create_with_provider(
        self,
        payment: Payment,
        values: PaymentCreate,
        *,
        now: datetime | None = None,
    ) -> ProviderPayment:
        """Create/retry the external intent using the already-persisted local key."""

        current = aware_utc(now)
        self.require_same_intent(payment, values)
        self._require_payment_state(payment, {PaymentStatus.CREATED})
        payment.attempts += 1
        command = PaymentCreateCommand(
            business_id=payment.business_id,
            appointment_id=payment.appointment_id,
            idempotency_key=payment.idempotency_key,
            amount=payment.amount,
            currency=payment.currency,
            payment_type=payment.payment_type,
            safe_metadata=payment.safe_metadata,
            return_url=str(values.return_url) if values.return_url is not None else None,
            description=values.description,
        )
        try:
            result = await self._provider.create_payment(command)
        except PaymentProviderError as exc:
            self._record_payment_error(payment, exc)
            raise
        self.apply_authoritative_payment(payment, result, now=current)
        return result

    def apply_creation_result(
        self,
        payment: Payment,
        result: ProviderPayment,
        *,
        now: datetime | None = None,
    ) -> None:
        """Persist a provider result fetched outside the database transaction."""

        self._require_payment_state(payment, {PaymentStatus.CREATED})
        payment.attempts += 1
        payment.last_error_code = None
        self.apply_authoritative_payment(payment, result, now=now)

    def apply_creation_failure(
        self,
        payment: Payment,
        error: PaymentProviderError,
    ) -> None:
        """Persist only a bounded error code after an out-of-transaction provider call."""

        self._require_payment_state(payment, {PaymentStatus.CREATED})
        payment.attempts += 1
        self._record_payment_error(payment, error)

    async def refresh_from_provider(
        self, payment: Payment, *, now: datetime | None = None
    ) -> ProviderPayment:
        """Authoritatively re-check provider state; webhook payload status is never trusted."""

        current = aware_utc(now)
        provider_payment_id = self._required_provider_payment_id(payment)
        payment.attempts += 1
        try:
            result = await self._provider.get_payment(provider_payment_id)
        except PaymentProviderError as exc:
            payment.last_error_code = exc.code
            raise
        self.apply_authoritative_payment(payment, result, now=current)
        return result

    async def cancel_with_provider(
        self,
        payment: Payment,
        *,
        idempotency_key: str,
        now: datetime | None = None,
    ) -> ProviderPayment:
        """Cancel only an unsettled payment and verify the provider response."""

        current = aware_utc(now)
        self._require_payment_state(payment, {PaymentStatus.CREATED, PaymentStatus.PENDING})
        provider_payment_id = self._required_provider_payment_id(payment)
        payment.attempts += 1
        command = PaymentCancelCommand(
            provider_payment_id=provider_payment_id,
            idempotency_key=idempotency_key,
            amount=payment.amount,
            currency=payment.currency,
            safe_metadata=payment.safe_metadata,
        )
        try:
            result = await self._provider.cancel_payment(command)
        except PaymentProviderError as exc:
            payment.last_error_code = exc.code
            raise
        if result.status is not PaymentStatus.CANCELLED:
            raise PaymentProviderProtocolError("provider_cancellation_not_confirmed")
        self.apply_authoritative_payment(payment, result, now=current)
        return result

    def apply_authoritative_payment(
        self,
        payment: Payment,
        result: ProviderPayment,
        *,
        now: datetime | None = None,
    ) -> None:
        """Validate identity, amount, currency and metadata before changing local state."""

        current = aware_utc(now)
        self._require_provider(result.provider)
        amount, currency = validate_money(result.amount, result.currency)
        if amount != payment.amount or currency != payment.currency:
            raise PaymentProviderProtocolError("provider_payment_money_mismatch")
        if payment.provider_payment_id not in (None, result.provider_payment_id):
            raise PaymentProviderProtocolError("provider_payment_id_mismatch")
        if any(
            result.safe_metadata.get(key) != value for key, value in payment.safe_metadata.items()
        ):
            raise PaymentProviderProtocolError("provider_payment_metadata_mismatch")

        target = result.status
        refund_states = {
            PaymentStatus.REFUND_PENDING,
            PaymentStatus.PARTIALLY_REFUNDED,
            PaymentStatus.REFUNDED,
        }
        if target is PaymentStatus.SUCCEEDED and payment.status in refund_states:
            target = payment.status
        require_payment_transition(payment.status, target)
        payment.provider_payment_id = result.provider_payment_id
        payment.status = target
        payment.confirmation_url = result.confirmation_url or payment.confirmation_url
        payment.last_error_code = None
        if target is PaymentStatus.SUCCEEDED and payment.paid_at is None:
            payment.paid_at = aware_utc(result.paid_at) if result.paid_at else current
        if target is PaymentStatus.CANCELLED and payment.cancelled_at is None:
            payment.cancelled_at = (
                aware_utc(result.cancelled_at) if result.cancelled_at else current
            )

    def confirm_manual_payment(self, payment: Payment, *, now: datetime | None = None) -> None:
        """Apply a human-confirmed transfer; RBAC and audit remain integration responsibilities."""

        if payment.provider is not PaymentMode.MANUAL:
            raise PaymentStateError("Ручное подтверждение доступно только для ручной оплаты.")
        self._require_payment_state(payment, {PaymentStatus.PENDING})
        require_payment_transition(payment.status, PaymentStatus.SUCCEEDED)
        payment.status = PaymentStatus.SUCCEEDED
        payment.paid_at = aware_utc(now)
        payment.last_error_code = None

    def new_refund(
        self,
        payment: Payment,
        values: RefundCreate,
        *,
        committed_or_pending_amount: Decimal,
    ) -> Refund:
        """Build a refund under a caller-held payment lock and reserve its amount."""

        self._require_provider(payment.provider)
        if payment.id is None or payment.provider_payment_id is None:
            raise PaymentStateError("Платёж ещё не сохранён или не создан у провайдера.")
        if values.business_id != payment.business_id or values.payment_id != payment.id:
            raise PaymentStateError("Возврат не принадлежит выбранному платежу.")
        self._require_payment_state(
            payment, {PaymentStatus.SUCCEEDED, PaymentStatus.PARTIALLY_REFUNDED}
        )
        amount, currency = validate_money(values.amount, values.currency)
        if currency != payment.currency:
            raise PaymentStateError("Refund currency must match the original payment.")
        reserved = committed_or_pending_amount.quantize(Decimal("0.01"))
        if reserved < payment.refunded_amount or reserved > payment.amount:
            raise PaymentStateError("Сумма ранее созданных возвратов некорректна.")
        remaining = payment.amount - reserved
        if amount > remaining:
            raise PaymentStateError("Сумма возврата превышает остаток платежа.")
        if amount < remaining and not self._provider.supports_partial_refunds:
            raise PaymentStateError("Провайдер не поддерживает частичный возврат.")
        require_payment_transition(payment.status, PaymentStatus.REFUND_PENDING)
        payment.status = PaymentStatus.REFUND_PENDING
        return Refund(
            business_id=values.business_id,
            payment_id=values.payment_id,
            provider=payment.provider,
            provider_refund_id=None,
            idempotency_key=values.idempotency_key,
            amount=amount,
            currency=currency,
            status=RefundStatus.PENDING,
            reason_code=values.reason_code,
            safe_metadata=validate_safe_metadata(values.safe_metadata),
            requested_by_user_id=values.requested_by_user_id,
            attempts=0,
            correlation_id=values.correlation_id,
        )

    async def refund_with_provider(
        self,
        payment: Payment,
        refund: Refund,
        *,
        now: datetime | None = None,
    ) -> ProviderRefund:
        """Submit an idempotent refund and apply only a fully verified response."""

        current = aware_utc(now)
        command = self.prepare_refund_submission(payment, refund)
        try:
            result = await self.submit_refund(command)
            self.apply_authoritative_refund(payment, refund, result, now=current)
        except PaymentProviderError as exc:
            self.apply_refund_submission_failure(payment, refund, exc, now=current)
            raise
        return result

    def prepare_refund_submission(
        self,
        payment: Payment,
        refund: Refund,
    ) -> PaymentRefundCommand:
        """Validate and persist an attempt before the caller leaves its transaction."""

        self._require_refund_belongs_to_payment(payment, refund)
        if refund.status is not RefundStatus.PENDING:
            raise PaymentStateError("Провайдеру можно отправить только ожидающий возврат.")
        provider_payment_id = self._required_provider_payment_id(payment)
        refund.attempts += 1
        return PaymentRefundCommand(
            provider_payment_id=provider_payment_id,
            idempotency_key=refund.idempotency_key,
            amount=refund.amount,
            currency=refund.currency,
            reason_code=refund.reason_code,
            safe_metadata=refund.safe_metadata,
        )

    async def submit_refund(self, command: PaymentRefundCommand) -> ProviderRefund:
        """Perform only the external idempotent provider call, without ORM mutation."""

        return await self._provider.refund_payment(command)

    def apply_refund_submission_failure(
        self,
        payment: Payment,
        refund: Refund,
        error: PaymentProviderError,
        *,
        now: datetime | None = None,
    ) -> None:
        """Apply a bounded provider failure after re-locking the local aggregates."""

        current = aware_utc(now)
        self._require_refund_belongs_to_payment(payment, refund)
        if refund.status is not RefundStatus.PENDING:
            return
        refund.last_error_code = error.code
        if not error.retryable:
            require_refund_transition(refund.status, RefundStatus.FAILED)
            refund.status = RefundStatus.FAILED
            refund.failed_at = current
            self._restore_payment_after_failed_refund(payment)

    async def refresh_refund_from_provider(
        self,
        payment: Payment,
        refund: Refund,
        *,
        now: datetime | None = None,
    ) -> ProviderRefund:
        """Resolve an asynchronous refund from authoritative provider state."""

        current = aware_utc(now)
        self._require_refund_belongs_to_payment(payment, refund)
        if refund.provider_refund_id is None:
            raise PaymentStateError("Возврат ещё не создан у провайдера.")
        refund.attempts += 1
        try:
            result = await self._provider.get_refund(refund.provider_refund_id)
        except PaymentProviderError as exc:
            refund.last_error_code = exc.code
            raise
        self.apply_authoritative_refund(payment, refund, result, now=current)
        return result

    def apply_authoritative_refund(
        self,
        payment: Payment,
        refund: Refund,
        result: ProviderRefund,
        *,
        now: datetime | None = None,
    ) -> None:
        """Apply a refund exactly once after provider identity and money checks."""

        current = aware_utc(now)
        self._require_refund_belongs_to_payment(payment, refund)
        self._require_provider(result.provider)
        provider_payment_id = self._required_provider_payment_id(payment)
        amount, currency = validate_money(result.amount, result.currency)
        if result.provider_payment_id != provider_payment_id:
            raise PaymentProviderProtocolError("provider_refund_payment_id_mismatch")
        if amount != refund.amount or currency != refund.currency:
            raise PaymentProviderProtocolError("provider_refund_money_mismatch")
        if refund.provider_refund_id not in (None, result.provider_refund_id):
            raise PaymentProviderProtocolError("provider_refund_id_mismatch")
        was_succeeded = refund.status is RefundStatus.SUCCEEDED
        require_refund_transition(refund.status, result.status)
        refund.provider_refund_id = result.provider_refund_id
        refund.status = result.status
        refund.last_error_code = None
        if result.status is RefundStatus.SUCCEEDED:
            if not was_succeeded:
                updated_refunded = payment.refunded_amount + refund.amount
                if updated_refunded > payment.amount:
                    raise PaymentStateError("Сумма успешных возвратов превышает платёж.")
                payment.refunded_amount = updated_refunded
            refund.succeeded_at = refund.succeeded_at or current
            target = (
                PaymentStatus.REFUNDED
                if payment.refunded_amount == payment.amount
                else PaymentStatus.PARTIALLY_REFUNDED
            )
            require_payment_transition(payment.status, target)
            payment.status = target
            if target is PaymentStatus.REFUNDED:
                payment.refunded_at = payment.refunded_at or current
        elif result.status in {RefundStatus.FAILED, RefundStatus.CANCELLED}:
            refund.failed_at = refund.failed_at or current
            self._restore_payment_after_failed_refund(payment)

    def confirm_manual_refund(
        self, payment: Payment, refund: Refund, *, now: datetime | None = None
    ) -> None:
        """Finish a manual refund after an authorized, audited human confirmation."""

        if payment.provider is not PaymentMode.MANUAL or refund.provider_refund_id is None:
            raise PaymentStateError("Ручное подтверждение возврата сейчас недоступно.")
        result = ProviderRefund(
            provider=PaymentMode.MANUAL,
            provider_refund_id=refund.provider_refund_id,
            provider_payment_id=self._required_provider_payment_id(payment),
            status=RefundStatus.SUCCEEDED,
            amount=refund.amount,
            currency=refund.currency,
        )
        self.apply_authoritative_refund(payment, refund, result, now=now)

    def new_webhook_event(
        self,
        *,
        business_id: int,
        event: ProviderWebhookEvent,
        received_at: datetime | None = None,
        retention_days: int = 30,
        correlation_id: str | None = None,
    ) -> PaymentWebhookEvent:
        """Persist only dedupe metadata and a digest, never the raw webhook payload."""

        self._require_provider(event.provider)
        if business_id <= 0 or not 1 <= retention_days <= 365:
            raise ValueError("invalid webhook business or retention")
        current = aware_utc(received_at)
        return PaymentWebhookEvent(
            business_id=business_id,
            payment_id=None,
            provider=event.provider,
            event_key=event.event_key,
            event_type=event.event_type,
            provider_object_id=event.provider_object_id,
            provider_payment_id=event.provider_payment_id,
            payload_sha256=event.payload_sha256,
            status=WebhookProcessingStatus.PENDING,
            received_at=current,
            processed_at=None,
            expires_at=current + timedelta(days=retention_days),
            attempts=0,
            correlation_id=correlation_id,
        )

    async def process_payment_webhook(
        self,
        payment: Payment,
        event: PaymentWebhookEvent,
        *,
        now: datetime | None = None,
    ) -> ProviderPayment:
        """Resolve a payment webhook through an authoritative provider status request."""

        current = aware_utc(now)
        self._require_webhook_matches_payment(payment, event)
        if not event.event_type.startswith("payment."):
            raise PaymentStateError("Webhook не является событием платежа.")
        event.attempts += 1
        try:
            result = await self.fetch_authoritative_payment(event.provider_payment_id)
        except PaymentProviderError as exc:
            self.apply_webhook_provider_failure(event, exc, now=current)
            raise
        self.apply_payment_webhook_result(payment, event, result, now=current)
        return result

    async def process_refund_webhook(
        self,
        payment: Payment,
        refund: Refund,
        event: PaymentWebhookEvent,
        *,
        now: datetime | None = None,
    ) -> ProviderRefund:
        """Resolve a refund webhook by fetching the provider refund object again."""

        current = aware_utc(now)
        self._require_webhook_matches_payment(payment, event)
        self._require_refund_belongs_to_payment(payment, refund)
        if not event.event_type.startswith("refund."):
            raise PaymentStateError("Webhook не является событием возврата.")
        if refund.provider_refund_id != event.provider_object_id:
            raise PaymentStateError("Webhook относится к другому возврату.")
        event.attempts += 1
        try:
            result = await self.fetch_authoritative_refund(event.provider_object_id)
        except PaymentProviderError as exc:
            self.apply_webhook_provider_failure(event, exc, now=current)
            raise
        self.apply_refund_webhook_result(payment, refund, event, result, now=current)
        return result

    async def fetch_authoritative_payment(self, provider_payment_id: str) -> ProviderPayment:
        """Fetch provider state without touching ORM objects or a database transaction."""

        return await self._provider.get_payment(provider_payment_id)

    async def fetch_authoritative_refund(self, provider_refund_id: str) -> ProviderRefund:
        """Fetch provider refund state without touching ORM objects or a transaction."""

        return await self._provider.get_refund(provider_refund_id)

    def apply_payment_webhook_result(
        self,
        payment: Payment,
        event: PaymentWebhookEvent,
        result: ProviderPayment,
        *,
        now: datetime | None = None,
    ) -> None:
        """Apply an authoritative payment result inside a fresh short transaction."""

        current = aware_utc(now)
        self._require_webhook_matches_payment(payment, event)
        if not event.event_type.startswith("payment."):
            raise PaymentStateError("Webhook не является событием платежа.")
        self.apply_authoritative_payment(payment, result, now=current)
        event.payment_id = payment.id
        event.status = WebhookProcessingStatus.PROCESSED
        event.processed_at = current
        event.last_error_code = None

    def apply_refund_webhook_result(
        self,
        payment: Payment,
        refund: Refund,
        event: PaymentWebhookEvent,
        result: ProviderRefund,
        *,
        now: datetime | None = None,
    ) -> None:
        """Apply an authoritative refund result inside a fresh short transaction."""

        current = aware_utc(now)
        self._require_webhook_matches_payment(payment, event)
        self._require_refund_belongs_to_payment(payment, refund)
        if not event.event_type.startswith("refund."):
            raise PaymentStateError("Webhook не является событием возврата.")
        if refund.provider_refund_id != event.provider_object_id:
            raise PaymentStateError("Webhook относится к другому возврату.")
        self.apply_authoritative_refund(payment, refund, result, now=current)
        event.payment_id = payment.id
        event.status = WebhookProcessingStatus.PROCESSED
        event.processed_at = current
        event.last_error_code = None

    @staticmethod
    def apply_webhook_provider_failure(
        event: PaymentWebhookEvent,
        error: PaymentProviderError,
        *,
        now: datetime | None = None,
    ) -> None:
        """Persist only a safe provider error after the external call has completed."""

        event.last_error_code = error.code
        if not error.retryable:
            event.status = WebhookProcessingStatus.FAILED
            event.processed_at = aware_utc(now)

    def _require_webhook_matches_payment(
        self, payment: Payment, event: PaymentWebhookEvent
    ) -> None:
        self._require_provider(payment.provider)
        if event.status is not WebhookProcessingStatus.PENDING:
            raise PaymentStateError("Webhook уже обработан.")
        if event.business_id != payment.business_id or event.provider is not payment.provider:
            raise PaymentStateError("Webhook относится к другому бизнесу или провайдеру.")
        if event.provider_payment_id != self._required_provider_payment_id(payment):
            raise PaymentStateError("Webhook относится к другому платежу.")

    def _require_refund_belongs_to_payment(self, payment: Payment, refund: Refund) -> None:
        self._require_provider(payment.provider)
        if (
            refund.business_id != payment.business_id
            or refund.payment_id != payment.id
            or refund.provider is not payment.provider
            or refund.currency != payment.currency
        ):
            raise PaymentStateError("Возврат относится к другому платежу или бизнесу.")

    def _restore_payment_after_failed_refund(self, payment: Payment) -> None:
        target = (
            PaymentStatus.PARTIALLY_REFUNDED
            if payment.refunded_amount > 0
            else PaymentStatus.SUCCEEDED
        )
        require_payment_transition(payment.status, target)
        payment.status = target

    def _record_payment_error(self, payment: Payment, error: PaymentProviderError) -> None:
        payment.last_error_code = error.code
        if not error.retryable:
            require_payment_transition(payment.status, PaymentStatus.FAILED)
            payment.status = PaymentStatus.FAILED

    def _require_provider(self, mode: PaymentMode) -> None:
        if mode is not self._provider.mode:
            raise PaymentStateError("Платёж относится к другому провайдеру.")

    @staticmethod
    def _require_payment_state(payment: Payment, allowed: set[PaymentStatus]) -> None:
        if payment.status not in allowed:
            raise PaymentStateError("Операция недоступна в текущем статусе платежа.")

    @staticmethod
    def _required_provider_payment_id(payment: Payment) -> str:
        if payment.provider_payment_id is None:
            raise PaymentStateError("Платёж ещё не создан у провайдера.")
        return payment.provider_payment_id

    @staticmethod
    def _intent_metadata(values: PaymentCreate) -> dict[str, str]:
        metadata = dict(values.safe_metadata)
        metadata.update(
            business_id=str(values.business_id),
            appointment_id=str(values.appointment_id),
        )
        return validate_safe_metadata(metadata)
