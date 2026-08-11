"""Transactional payment workflows shared by HTTP and Telegram boundaries."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from types import TracebackType
from typing import Never, Protocol, Self

from app.database.models.appointment import Appointment, AppointmentStatusHistory
from app.database.models.payment import Payment, PaymentWebhookEvent, Refund
from app.domain.appointments import ensure_appointment_transition
from app.domain.enums import (
    AppointmentStatus,
    ManualPaymentStatus,
    PaymentMode,
    PaymentStatus,
    RefundStatus,
)
from app.domain.errors import DomainError, EntityNotFoundError
from app.domain.payments import PaymentStateError, WebhookProcessingStatus, aware_utc
from app.payments.providers.base import PaymentProviderError, ProviderWebhookEvent
from app.repositories.audit_repository import AuditRepository
from app.repositories.payment_repository import PaymentRepository
from app.repositories.reservation_repository import ReservationRepository
from app.schemas.authorization import StaffContext, StaffPermission
from app.schemas.payment import PaymentView, RefundCreate, RefundView
from app.services.payment_service import PaymentService
from app.services.reservation_service import ReservationService

_SAFE_CORRELATION_ID = re.compile(r"^[A-Za-z0-9._:-]{1,64}$")
_TERMINAL_WEBHOOK_STATUSES = frozenset(
    {
        WebhookProcessingStatus.PROCESSED,
        WebhookProcessingStatus.IGNORED,
        WebhookProcessingStatus.FAILED,
    }
)


@dataclass(frozen=True, slots=True)
class WebhookDisposition:
    """Safe webhook outcome without provider IDs or notification content."""

    duplicate: bool


class WebhookProcessingError(RuntimeError):
    """Safe orchestration failure controlling whether the provider should retry."""

    def __init__(self, *, retryable: bool) -> None:
        self.retryable = retryable
        super().__init__("webhook_processing_failed")


@dataclass(frozen=True, slots=True)
class RefundOutcome:
    refund: RefundView
    created: bool


class PaymentUnitOfWork(Protocol):
    """Minimal transaction port; the concrete SQLAlchemy UoW satisfies it."""

    business_id: int
    payments: PaymentRepository
    reservations: ReservationRepository
    audit: AuditRepository

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    async def commit(self) -> None: ...


PaymentUnitOfWorkFactory = Callable[[], PaymentUnitOfWork]


class AuthorizationPort(Protocol):
    async def authorize(
        self,
        *,
        business_id: int,
        telegram_id: int,
        permission: StaffPermission | None = None,
    ) -> StaffContext: ...


class YooKassaWebhookLifecycleCoordinator:
    """Deduplicate, verify via authoritative GET, and reconcile a paid reservation."""

    def __init__(
        self,
        uow_factory: PaymentUnitOfWorkFactory,
        payment_service: PaymentService,
        *,
        business_id: int,
        retention_days: int = 30,
    ) -> None:
        if business_id <= 0:
            raise ValueError("business_id must be positive")
        if not 1 <= retention_days <= 365:
            raise ValueError("retention_days must be between 1 and 365")
        if payment_service.provider_mode is not PaymentMode.YOOKASSA:
            raise ValueError("YooKassa webhook coordinator requires the YooKassa provider")
        self._uow_factory = uow_factory
        self._payment_service = payment_service
        self._business_id = business_id
        self._retention_days = retention_days

    async def process_untrusted_notification(
        self,
        event: ProviderWebhookEvent,
        *,
        correlation_id: str,
        now: datetime | None = None,
    ) -> WebhookDisposition:
        """Process one bounded envelope; payload status and amounts are never consulted."""

        current = aware_utc(now)
        correlation = _correlation_id(correlation_id)
        if event.provider is not PaymentMode.YOOKASSA:
            raise WebhookProcessingError(retryable=False)

        payment_to_reconcile: int | None = None
        duplicate = False
        async with self._uow_factory() as uow:
            self._require_scope(uow)
            draft = self._payment_service.new_webhook_event(
                business_id=self._business_id,
                event=event,
                received_at=current,
                retention_days=self._retention_days,
                correlation_id=correlation,
            )
            stored, inserted = await uow.payments.add_webhook_if_absent(draft)
            duplicate = not inserted
            if not inserted:
                locked = await uow.payments.get_webhook_by_event_key(
                    event.provider,
                    event.event_key,
                    for_update=True,
                )
                if locked is None:
                    raise WebhookProcessingError(retryable=True)
                stored = locked
                if not self._same_envelope(stored, event):
                    raise WebhookProcessingError(retryable=False)
                if stored.status in _TERMINAL_WEBHOOK_STATUSES:
                    if (
                        stored.status is WebhookProcessingStatus.PROCESSED
                        and stored.event_type.startswith("payment.")
                    ):
                        payment_to_reconcile = stored.payment_id
                else:
                    payment_to_reconcile = await self._process_locked_event(
                        uow,
                        stored,
                        current,
                    )
                    await uow.commit()
            else:
                payment_to_reconcile = await self._process_locked_event(uow, stored, current)
                await uow.commit()

        if payment_to_reconcile is not None:
            await self._reconcile_paid_reservation(
                payment_to_reconcile,
                now=current,
                correlation_id=correlation,
                actor_user_id=None,
            )
        return WebhookDisposition(duplicate=duplicate)

    async def _process_locked_event(
        self,
        uow: PaymentUnitOfWork,
        event: PaymentWebhookEvent,
        now: datetime,
    ) -> int | None:
        payment = await uow.payments.get_by_provider_id(
            event.provider,
            event.provider_payment_id,
            for_update=True,
        )
        if payment is None:
            await self._persist_retryable_lookup_failure(uow, event, "payment_not_found")

        try:
            if event.event_type.startswith("payment."):
                await self._payment_service.process_payment_webhook(payment, event, now=now)
            elif event.event_type.startswith("refund."):
                refund = await uow.payments.get_refund_by_provider_id(
                    event.provider,
                    event.provider_object_id,
                    for_update=True,
                )
                if refund is None:
                    await self._persist_retryable_lookup_failure(uow, event, "refund_not_found")
                await self._payment_service.process_refund_webhook(
                    payment,
                    refund,
                    event,
                    now=now,
                )
                await _sync_refund_appointment_status(
                    uow,
                    payment,
                    actor_user_id=None,
                )
            else:
                event.status = WebhookProcessingStatus.IGNORED
                event.processed_at = now
                event.last_error_code = "event_type_unsupported"
        except PaymentProviderError as exc:
            await uow.commit()
            raise WebhookProcessingError(retryable=exc.retryable) from None
        except PaymentStateError:
            event.status = WebhookProcessingStatus.FAILED
            event.processed_at = now
            event.last_error_code = "payment_state_rejected"
            await uow.commit()
            raise WebhookProcessingError(retryable=False) from None

        await uow.audit.add(
            actor_user_id=None,
            action="payment.webhook_processed",
            entity_type="payment_webhook_event",
            entity_id=str(event.id),
            changes={
                "payment_id": payment.id,
                "event_type": event.event_type,
                "status": event.status.value,
            },
            correlation_id=event.correlation_id,
        )
        if event.event_type.startswith("payment.") and payment.status is PaymentStatus.SUCCEEDED:
            return payment.id
        return None

    @staticmethod
    async def _persist_retryable_lookup_failure(
        uow: PaymentUnitOfWork,
        event: PaymentWebhookEvent,
        code: str,
    ) -> Never:
        event.attempts += 1
        event.last_error_code = code
        await uow.commit()
        raise WebhookProcessingError(retryable=True)

    async def _reconcile_paid_reservation(
        self,
        payment_id: int,
        *,
        now: datetime,
        correlation_id: str | None,
        actor_user_id: int | None,
    ) -> None:
        try:
            async with self._uow_factory() as uow:
                self._require_scope(uow)
                payment = await uow.payments.get(payment_id)
                if payment is None or payment.status is not PaymentStatus.SUCCEEDED:
                    return
                reservation = await uow.reservations.get_active_for_appointment(
                    payment.appointment_id,
                    for_update=True,
                )
                if reservation is None:
                    return
                service = ReservationService(uow.reservations, uow.payments, uow.audit)
                await service.consume(
                    reservation.id,
                    appointment_id=payment.appointment_id,
                    actor_user_id=actor_user_id,
                    now=now,
                    correlation_id=correlation_id,
                )
                await uow.commit()
        except DomainError:
            raise WebhookProcessingError(retryable=True) from None

    def _require_scope(self, uow: PaymentUnitOfWork) -> None:
        if uow.business_id != self._business_id:
            raise ValueError("unit of work belongs to another business")

    @staticmethod
    def _same_envelope(stored: PaymentWebhookEvent, event: ProviderWebhookEvent) -> bool:
        return (
            stored.provider is event.provider
            and stored.event_key == event.event_key
            and stored.event_type == event.event_type
            and stored.provider_object_id == event.provider_object_id
            and stored.provider_payment_id == event.provider_payment_id
            and stored.payload_sha256 == event.payload_sha256
        )


class ManualPaymentApprovalCoordinator:
    """Live-authorized, audited manual payment approval with replay repair."""

    def __init__(
        self,
        uow_factory: PaymentUnitOfWorkFactory,
        authorization: AuthorizationPort,
        payment_service: PaymentService,
    ) -> None:
        if payment_service.provider_mode is not PaymentMode.MANUAL:
            raise ValueError("manual approval coordinator requires the manual provider")
        self._uow_factory = uow_factory
        self._authorization = authorization
        self._payment_service = payment_service

    async def approve(
        self,
        actor: StaffContext,
        payment_id: int,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> PaymentView:
        current = aware_utc(now)
        correlation = _correlation_id(correlation_id)
        live_actor = await _authorize_actor(
            self._authorization,
            actor,
            StaffPermission.APPROVE_PREPAYMENTS,
        )
        transitioned = False
        async with self._uow_factory() as uow:
            _require_actor_scope(uow, live_actor)
            payment = await uow.payments.get(payment_id, for_update=True)
            if payment is None:
                raise EntityNotFoundError("payment not found")
            if payment.provider is not PaymentMode.MANUAL:
                raise PaymentStateError("manual approval requires a manual payment")
            if payment.manual_status is ManualPaymentStatus.CONFIRMED:
                if payment.status is not PaymentStatus.SUCCEEDED:
                    raise PaymentStateError("manual payment state is inconsistent")
            elif payment.manual_status not in {
                ManualPaymentStatus.CLIENT_REPORTED,
                ManualPaymentStatus.REVIEW_PENDING,
            }:
                raise PaymentStateError("Клиент ещё не сообщил об оплате.")
            if payment.status is not PaymentStatus.SUCCEEDED:
                self._payment_service.confirm_manual_payment(payment, now=current)
                payment.manual_status = ManualPaymentStatus.CONFIRMED
                payment.reviewed_at = current
                payment.reviewed_by_user_id = live_actor.user_id
                transitioned = True
                await uow.audit.add(
                    actor_user_id=live_actor.user_id,
                    action="payment.manual_approved",
                    entity_type="payment",
                    entity_id=str(payment.id),
                    changes={
                        "appointment_id": payment.appointment_id,
                        "provider": payment.provider.value,
                        "status": payment.status.value,
                    },
                    correlation_id=correlation,
                )
            view = PaymentView.model_validate(payment)
            if transitioned:
                await uow.commit()

        await _consume_paid_reservation(
            self._uow_factory,
            business_id=live_actor.business_id,
            payment_id=payment_id,
            actor_user_id=live_actor.user_id,
            now=current,
            correlation_id=correlation,
        )
        return view


class RefundCoordinator:
    """Serialize refund totals and external idempotency behind live RBAC checks."""

    def __init__(
        self,
        uow_factory: PaymentUnitOfWorkFactory,
        authorization: AuthorizationPort,
        payment_service: PaymentService,
    ) -> None:
        self._uow_factory = uow_factory
        self._authorization = authorization
        self._payment_service = payment_service

    async def create_and_submit(
        self,
        actor: StaffContext,
        values: RefundCreate,
        *,
        now: datetime | None = None,
    ) -> RefundOutcome:
        current = aware_utc(now)
        correlation = _correlation_id(values.correlation_id)
        live_actor = await _authorize_actor(
            self._authorization,
            actor,
            StaffPermission.REFUND_PAYMENTS,
        )
        if values.business_id != live_actor.business_id:
            raise PaymentStateError("refund belongs to another business")
        if values.requested_by_user_id not in (None, live_actor.user_id):
            raise PaymentStateError("refund actor does not match the authorized staff member")
        normalized = values.model_copy(
            update={
                "requested_by_user_id": live_actor.user_id,
                "correlation_id": correlation,
            }
        )

        refund_id, created = await self._ensure_refund(live_actor, normalized)
        view = await self._submit_pending_refund(
            live_actor,
            payment_id=normalized.payment_id,
            refund_id=refund_id,
            now=current,
            correlation_id=normalized.correlation_id,
        )
        return RefundOutcome(refund=view, created=created)

    async def approve_manual_refund(
        self,
        actor: StaffContext,
        refund_id: int,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> RefundView:
        current = aware_utc(now)
        correlation = _correlation_id(correlation_id)
        live_actor = await _authorize_actor(
            self._authorization,
            actor,
            StaffPermission.REFUND_PAYMENTS,
        )
        async with self._uow_factory() as uow:
            _require_actor_scope(uow, live_actor)
            initial = await uow.payments.get_refund(refund_id)
            if initial is None:
                raise EntityNotFoundError("refund not found")
            payment = await uow.payments.get(initial.payment_id, for_update=True)
            if payment is None:
                raise EntityNotFoundError("payment not found")
            refund = await uow.payments.get_refund(refund_id, for_update=True)
            if refund is None or refund.payment_id != payment.id:
                raise EntityNotFoundError("refund not found")
            if (
                self._payment_service.provider_mode is not PaymentMode.MANUAL
                or payment.provider is not PaymentMode.MANUAL
                or refund.provider is not PaymentMode.MANUAL
                or refund.business_id != live_actor.business_id
                or refund.currency != payment.currency
            ):
                raise PaymentStateError("manual approval requires a manual refund")
            if refund.status is not RefundStatus.SUCCEEDED:
                self._payment_service.confirm_manual_refund(payment, refund, now=current)
                await self._sync_refund_result(
                    uow,
                    payment,
                    actor_user_id=live_actor.user_id,
                )
                await uow.audit.add(
                    actor_user_id=live_actor.user_id,
                    action="refund.manual_approved",
                    entity_type="refund",
                    entity_id=str(refund.id),
                    changes={"payment_id": payment.id, "status": refund.status.value},
                    correlation_id=correlation,
                )
                view = RefundView.model_validate(refund)
                await uow.commit()
                return view
            return RefundView.model_validate(refund)

    async def _ensure_refund(
        self,
        actor: StaffContext,
        values: RefundCreate,
    ) -> tuple[int, bool]:
        async with self._uow_factory() as uow:
            _require_actor_scope(uow, actor)
            payment = await uow.payments.get(values.payment_id, for_update=True)
            if payment is None:
                raise EntityNotFoundError("payment not found")
            if payment.provider is not self._payment_service.provider_mode:
                raise PaymentStateError("payment belongs to another provider")
            existing = await uow.payments.get_refund_by_idempotency_key(
                values.idempotency_key,
                for_update=True,
            )
            if existing is not None:
                if existing.provider is not payment.provider:
                    raise PaymentStateError("refund belongs to another provider")
                self._require_same_refund(existing, values)
                return existing.id, False

            pending = await uow.payments.sum_pending_refunds(payment.id)
            reserved = payment.refunded_amount + pending
            prior_payment_status = payment.status
            draft = self._payment_service.new_refund(
                payment,
                values,
                committed_or_pending_amount=reserved,
            )
            refund, inserted = await uow.payments.add_refund_if_absent(draft)
            if not inserted:
                payment.status = prior_payment_status
                if refund.provider is not payment.provider:
                    raise PaymentStateError("refund belongs to another provider")
                self._require_same_refund(refund, values)
                return refund.id, False
            await self._transition_refund_appointment(
                uow,
                payment,
                AppointmentStatus.REFUND_PENDING,
                actor_user_id=actor.user_id,
                reason="refund_requested",
            )
            await uow.audit.add(
                actor_user_id=actor.user_id,
                action="refund.requested",
                entity_type="refund",
                entity_id=str(refund.id),
                changes={
                    "payment_id": payment.id,
                    "status": refund.status.value,
                    "reason_code": refund.reason_code,
                },
                correlation_id=values.correlation_id,
            )
            refund_id = refund.id
            await uow.commit()
            return refund_id, True

    async def _submit_pending_refund(
        self,
        actor: StaffContext,
        *,
        payment_id: int,
        refund_id: int,
        now: datetime,
        correlation_id: str | None,
    ) -> RefundView:
        async with self._uow_factory() as uow:
            _require_actor_scope(uow, actor)
            payment = await uow.payments.get(payment_id, for_update=True)
            if payment is None:
                raise EntityNotFoundError("payment not found")
            refund = await uow.payments.get_refund(refund_id, for_update=True)
            if refund is None or refund.payment_id != payment.id:
                raise EntityNotFoundError("refund not found")
            if (
                payment.provider is not self._payment_service.provider_mode
                or refund.provider is not payment.provider
                or refund.business_id != actor.business_id
                or refund.currency != payment.currency
            ):
                raise PaymentStateError("refund belongs to another provider or business")
            if refund.status is not RefundStatus.PENDING or refund.provider_refund_id is not None:
                return RefundView.model_validate(refund)
            try:
                await self._payment_service.refund_with_provider(payment, refund, now=now)
            except PaymentProviderError:
                await uow.commit()
                raise
            await self._sync_refund_result(
                uow,
                payment,
                actor_user_id=actor.user_id,
            )
            await uow.audit.add(
                actor_user_id=actor.user_id,
                action="refund.submitted",
                entity_type="refund",
                entity_id=str(refund.id),
                changes={"payment_id": payment.id, "status": refund.status.value},
                correlation_id=correlation_id,
            )
            view = RefundView.model_validate(refund)
            await uow.commit()
            return view

    async def _sync_refund_result(
        self,
        uow: PaymentUnitOfWork,
        payment: Payment,
        *,
        actor_user_id: int | None,
    ) -> None:
        await _sync_refund_appointment_status(
            uow,
            payment,
            actor_user_id=actor_user_id,
        )

    @staticmethod
    async def _transition_refund_appointment(
        uow: PaymentUnitOfWork,
        payment: Payment,
        target: AppointmentStatus,
        *,
        actor_user_id: int | None,
        reason: str,
    ) -> Appointment:
        appointment = await uow.reservations.get_appointment_for_update(payment.appointment_id)
        if appointment is None:
            raise EntityNotFoundError("appointment not found")
        previous = appointment.status
        ensure_appointment_transition(previous, target)
        if previous is not target:
            appointment.status = target
            await uow.reservations.add_history(
                AppointmentStatusHistory(
                    appointment_id=appointment.id,
                    previous_status=previous,
                    new_status=target,
                    changed_by_user_id=actor_user_id,
                    reason=reason,
                )
            )
        return appointment

    @staticmethod
    def _require_same_refund(refund: Refund, values: RefundCreate) -> None:
        expected = (
            values.business_id,
            values.payment_id,
            values.amount,
            values.currency,
            values.idempotency_key,
            values.reason_code,
            values.requested_by_user_id,
            values.safe_metadata,
        )
        actual = (
            refund.business_id,
            refund.payment_id,
            refund.amount,
            refund.currency,
            refund.idempotency_key,
            refund.reason_code,
            refund.requested_by_user_id,
            refund.safe_metadata,
        )
        if actual != expected:
            raise PaymentStateError("refund idempotency key is bound to another request")


async def _authorize_actor(
    authorization: AuthorizationPort,
    actor: StaffContext,
    permission: StaffPermission,
) -> StaffContext:
    return await authorization.authorize(
        business_id=actor.business_id,
        telegram_id=actor.telegram_id,
        permission=permission,
    )


def _require_actor_scope(uow: PaymentUnitOfWork, actor: StaffContext) -> None:
    if uow.business_id != actor.business_id:
        raise ValueError("unit of work belongs to another business")


def _correlation_id(value: str | None) -> str | None:
    if value is None:
        return None
    if not _SAFE_CORRELATION_ID.fullmatch(value):
        raise ValueError("correlation_id must be 1-64 safe ASCII characters")
    return value


async def _consume_paid_reservation(
    uow_factory: PaymentUnitOfWorkFactory,
    *,
    business_id: int,
    payment_id: int,
    actor_user_id: int | None,
    now: datetime,
    correlation_id: str | None,
) -> None:
    async with uow_factory() as uow:
        if uow.business_id != business_id:
            raise ValueError("unit of work belongs to another business")
        payment = await uow.payments.get(payment_id)
        if payment is None or payment.status is not PaymentStatus.SUCCEEDED:
            return
        reservation = await uow.reservations.get_active_for_appointment(
            payment.appointment_id,
            for_update=True,
        )
        if reservation is None:
            return
        service = ReservationService(uow.reservations, uow.payments, uow.audit)
        await service.consume(
            reservation.id,
            appointment_id=payment.appointment_id,
            actor_user_id=actor_user_id,
            now=now,
            correlation_id=correlation_id,
        )
        await uow.commit()


async def _sync_refund_appointment_status(
    uow: PaymentUnitOfWork,
    payment: Payment,
    *,
    actor_user_id: int | None,
) -> None:
    if payment.status is PaymentStatus.PARTIALLY_REFUNDED:
        target = AppointmentStatus.PARTIALLY_REFUNDED
    elif payment.status is PaymentStatus.REFUNDED:
        target = AppointmentStatus.REFUNDED
    else:
        return
    appointment = await uow.reservations.get_appointment_for_update(payment.appointment_id)
    if appointment is None:
        raise EntityNotFoundError("appointment not found")
    previous = appointment.status
    ensure_appointment_transition(previous, target)
    if previous is target:
        return
    appointment.status = target
    await uow.reservations.add_history(
        AppointmentStatusHistory(
            appointment_id=appointment.id,
            previous_status=previous,
            new_status=target,
            changed_by_user_id=actor_user_id,
            reason="refund_succeeded",
        )
    )
