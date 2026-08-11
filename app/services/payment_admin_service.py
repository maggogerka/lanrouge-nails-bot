"""Live-authorized administration facade for payment operations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime

from app.database.models import AppointmentStatusHistory
from app.domain.appointments import ensure_appointment_transition
from app.domain.enums import (
    AppointmentStatus,
    AvailabilityWindowStatus,
    ManualPaymentStatus,
    PaymentMode,
    PaymentStatus,
    RefundStatus,
    ReservationStatus,
    StaffRole,
)
from app.domain.errors import EntityNotFoundError
from app.domain.payments import PaymentStateError, aware_utc, require_payment_transition
from app.domain.reservations import ensure_reservation_transition
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.authorization import StaffContext, StaffPermission
from app.schemas.payment import PaymentSettingsView, PaymentView, RefundCreate, RefundView
from app.services.authorization_service import AuthorizationService
from app.services.payment_coordinator import (
    ManualPaymentApprovalCoordinator,
    RefundCoordinator,
    RefundOutcome,
)

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


@dataclass(frozen=True, slots=True)
class ManualPaymentDecision:
    payment: PaymentView
    client_telegram_id: int
    changed: bool


@dataclass(frozen=True, slots=True)
class ManualReceiptAccess:
    telegram_file_id: str
    media_type: str


class PaymentAdministrationService:
    """Keep Telegram handlers free from financial state transitions."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        authorization_service: AuthorizationService,
        manual_approval: ManualPaymentApprovalCoordinator,
        refund_coordinators: Mapping[PaymentMode, RefundCoordinator] | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._authorization = authorization_service
        self._manual_approval = manual_approval
        self._refund_coordinators = dict(refund_coordinators or {})
        self._available_modes = frozenset(self._refund_coordinators)

    async def get_settings(self, actor: StaffContext) -> PaymentSettingsView:
        live_actor = await self._authorization.authorize(
            business_id=actor.business_id,
            telegram_id=actor.telegram_id,
            permission=StaffPermission.VIEW_PREPAYMENTS,
        )
        async with self._unit_of_work_factory() as unit_of_work:
            if unit_of_work.business_id != live_actor.business_id:
                raise RuntimeError("payment unit of work tenant mismatch")
            settings = await unit_of_work.reservations.payment_settings()
            if settings is None:
                raise EntityNotFoundError("Настройки оплаты не найдены.")
            return PaymentSettingsView.model_validate(settings)

    async def set_mode(
        self,
        actor: StaffContext,
        mode: PaymentMode,
        *,
        confirmed: bool,
        correlation_id: str | None = None,
    ) -> PaymentSettingsView:
        if not confirmed:
            raise PaymentStateError("Требуется явное подтверждение изменения режима.")
        live_actor = await self._authorization.authorize(
            business_id=actor.business_id,
            telegram_id=actor.telegram_id,
            permission=StaffPermission.CHANGE_PAYMENT_SETTINGS,
        )
        if mode is not PaymentMode.DISABLED and mode not in self._available_modes:
            raise PaymentStateError("Платёжный провайдер не настроен на сервере.")
        async with self._unit_of_work_factory() as unit_of_work:
            if unit_of_work.business_id != live_actor.business_id:
                raise RuntimeError("payment unit of work tenant mismatch")
            settings = await unit_of_work.reservations.payment_settings(for_update=True)
            flags = await unit_of_work.features.get()
            if settings is None or flags is None:
                raise EntityNotFoundError("Настройки оплаты не найдены.")
            if mode is PaymentMode.MANUAL:
                if not (settings.manual_payment_instructions or "").strip():
                    raise PaymentStateError("Сначала заполните инструкции ручной оплаты.")
                if not flags.prepayment or not flags.manual_payments:
                    raise PaymentStateError(
                        "Сначала включите «Предоплата» и «Ручная оплата» в функциях бота."
                    )
            if mode is PaymentMode.YOOKASSA and (
                not flags.prepayment or not flags.yookassa_payments
            ):
                raise PaymentStateError(
                    "Сначала включите «Предоплата» и «YooKassa» в функциях бота."
                )
            previous = settings.mode
            settings.mode = mode
            settings.version += 1
            await unit_of_work.audit.add(
                actor_user_id=live_actor.user_id,
                action="payment.settings_mode_changed",
                entity_type="business_payment_settings",
                entity_id=str(live_actor.business_id),
                changes={"previous": previous.value, "new": mode.value},
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return PaymentSettingsView.model_validate(settings)

    async def set_manual_instructions(
        self,
        actor: StaffContext,
        instructions: str,
        *,
        correlation_id: str | None = None,
    ) -> PaymentSettingsView:
        live_actor = await self._authorization.authorize(
            business_id=actor.business_id,
            telegram_id=actor.telegram_id,
            permission=StaffPermission.EDIT_PAYMENT_INSTRUCTIONS,
        )
        normalized = instructions.strip()
        if not 1 <= len(normalized) <= 2000:
            raise PaymentStateError("Инструкция должна содержать от 1 до 2000 символов.")
        forbidden = ("cvv", "cvc", "sms-код", "смс-код", "срок действия карты")
        if any(marker in normalized.casefold() for marker in forbidden):
            raise PaymentStateError("Нельзя запрашивать CVV/CVC, SMS-коды или срок действия карты.")
        async with self._unit_of_work_factory() as unit_of_work:
            if unit_of_work.business_id != live_actor.business_id:
                raise RuntimeError("payment unit of work tenant mismatch")
            settings = await unit_of_work.reservations.payment_settings(for_update=True)
            if settings is None:
                raise EntityNotFoundError("Настройки оплаты не найдены.")
            settings.manual_payment_instructions = normalized
            settings.version += 1
            await unit_of_work.audit.add(
                actor_user_id=live_actor.user_id,
                action="payment.manual_instructions_changed",
                entity_type="business_payment_settings",
                entity_id=str(live_actor.business_id),
                changes={"configured": True},
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return PaymentSettingsView.model_validate(settings)

    async def set_reservation_ttl(
        self,
        actor: StaffContext,
        ttl_minutes: int,
        *,
        correlation_id: str | None = None,
    ) -> PaymentSettingsView:
        live_actor = await self._authorization.authorize(
            business_id=actor.business_id,
            telegram_id=actor.telegram_id,
            permission=StaffPermission.EDIT_PAYMENT_TIMERS,
        )
        if not 5 <= ttl_minutes <= 60:
            raise PaymentStateError("Время оплаты должно быть от 5 до 60 минут.")
        async with self._unit_of_work_factory() as unit_of_work:
            if unit_of_work.business_id != live_actor.business_id:
                raise RuntimeError("payment unit of work tenant mismatch")
            settings = await unit_of_work.reservations.payment_settings(for_update=True)
            if settings is None:
                raise EntityNotFoundError("Настройки оплаты не найдены.")
            previous = settings.reservation_ttl_minutes
            settings.reservation_ttl_minutes = ttl_minutes
            settings.client_payment_reminder_minutes = [
                offset for offset in (5, 10) if offset < ttl_minutes
            ]
            settings.version += 1
            await unit_of_work.audit.add(
                actor_user_id=live_actor.user_id,
                action="payment.timer_changed",
                entity_type="business_payment_settings",
                entity_id=str(live_actor.business_id),
                changes={
                    "previous_minutes": previous,
                    "new_minutes": ttl_minutes,
                    "client_reminders": settings.client_payment_reminder_minutes,
                },
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return PaymentSettingsView.model_validate(settings)

    async def list_recent(
        self,
        actor: StaffContext,
        *,
        limit: int = 30,
    ) -> tuple[PaymentView, ...]:
        live_actor = await self._authorization.authorize(
            business_id=actor.business_id,
            telegram_id=actor.telegram_id,
            permission=StaffPermission.VIEW_PREPAYMENTS,
        )
        async with self._unit_of_work_factory() as unit_of_work:
            if unit_of_work.business_id != live_actor.business_id:
                raise RuntimeError("payment unit of work tenant mismatch")
            rows = await unit_of_work.payments.list_recent(
                statuses={
                    PaymentStatus.CREATED,
                    PaymentStatus.PENDING,
                    PaymentStatus.SUCCEEDED,
                    PaymentStatus.REFUND_PENDING,
                    PaymentStatus.PARTIALLY_REFUNDED,
                    PaymentStatus.REFUNDED,
                },
                staff_member_id=(
                    live_actor.staff_member_id if live_actor.role is StaffRole.MASTER else None
                ),
                limit=limit,
            )
            return tuple(PaymentView.model_validate(row) for row in rows)

    async def approve_manual(
        self,
        actor: StaffContext,
        payment_id: int,
        *,
        correlation_id: str | None = None,
    ) -> PaymentView:
        return await self._manual_approval.approve(
            actor,
            payment_id,
            correlation_id=correlation_id,
        )

    async def reject_manual(
        self,
        actor: StaffContext,
        payment_id: int,
        *,
        reason: str | None,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ManualPaymentDecision:
        """Reject once under row locks and release the held window exactly once."""

        live_actor = await self._authorization.authorize(
            business_id=actor.business_id,
            telegram_id=actor.telegram_id,
            permission=StaffPermission.REJECT_PREPAYMENTS,
        )
        normalized_reason = (reason or "").strip()
        if normalized_reason == "-":
            normalized_reason = ""
        if len(normalized_reason) > 500:
            raise PaymentStateError("Причина отклонения не должна превышать 500 символов.")
        current = aware_utc(now)
        async with self._unit_of_work_factory() as uow:
            if uow.business_id != live_actor.business_id:
                raise RuntimeError("payment unit of work tenant mismatch")
            hint = await uow.payments.get(payment_id)
            if hint is None:
                raise EntityNotFoundError("Платёж не найден.")
            appointment_hint = await uow.appointments.get(hint.appointment_id)
            if appointment_hint is None:
                raise EntityNotFoundError("Связанная запись не найдена.")
            self._require_assigned_if_master(live_actor, appointment_hint.staff_member_id)
            reservation = await uow.reservations.get_active_for_appointment(
                appointment_hint.id, for_update=True
            )
            payment = await uow.payments.get(payment_id, for_update=True)
            appointment = await uow.appointments.get(appointment_hint.id, for_update=True)
            if payment is None or appointment is None:
                raise EntityNotFoundError("Платёж не найден.")
            client = await uow.users.get_by_id(appointment.client_id)
            if client is None:
                raise EntityNotFoundError("Клиент не найден.")
            if payment.manual_status is ManualPaymentStatus.REJECTED:
                return ManualPaymentDecision(
                    PaymentView.model_validate(payment), client.telegram_id, False
                )
            if (
                payment.provider is not PaymentMode.MANUAL
                or payment.manual_status
                not in {
                    ManualPaymentStatus.CLIENT_REPORTED,
                    ManualPaymentStatus.REVIEW_PENDING,
                }
                or payment.status is not PaymentStatus.PENDING
                or reservation is None
                or reservation.status is not ReservationStatus.AWAITING_REVIEW
            ):
                raise PaymentStateError("Эту предоплату уже нельзя отклонить.")
            window = await uow.reservations.get_window_for_update(reservation.window_id)
            if window is None:
                raise EntityNotFoundError("Окно записи не найдено.")

            require_payment_transition(payment.status, PaymentStatus.FAILED)
            payment.status = PaymentStatus.FAILED
            payment.manual_status = ManualPaymentStatus.REJECTED
            payment.reviewed_at = current
            payment.reviewed_by_user_id = live_actor.user_id
            payment.rejection_reason = normalized_reason or None
            payment.last_error_code = "manual_rejected"
            ensure_appointment_transition(appointment.status, AppointmentStatus.CANCELLED_BY_ADMIN)
            previous = appointment.status
            appointment.status = AppointmentStatus.CANCELLED_BY_ADMIN
            appointment.cancelled_at = current
            appointment.cancellation_reason = normalized_reason or "manual_payment_rejected"
            appointment.reservation_expires_at = None
            await uow.appointments.add_history(
                AppointmentStatusHistory(
                    appointment_id=appointment.id,
                    previous_status=previous,
                    new_status=appointment.status,
                    changed_by_user_id=live_actor.user_id,
                    reason="manual_payment_rejected",
                )
            )
            ensure_reservation_transition(reservation.status, ReservationStatus.CANCELLED)
            reservation.status = ReservationStatus.CANCELLED
            reservation.cancelled_at = current
            if window.status in {
                AvailabilityWindowStatus.RESERVED,
                AvailabilityWindowStatus.BOOKED,
            }:
                window.status = AvailabilityWindowStatus.OPEN
            await uow.audit.add(
                actor_user_id=live_actor.user_id,
                action="payment.manual_rejected",
                entity_type="payment",
                entity_id=str(payment.id),
                changes={
                    "appointment_id": appointment.id,
                    "has_reason": bool(normalized_reason),
                    "window_released": window.status is AvailabilityWindowStatus.OPEN,
                },
                correlation_id=correlation_id,
            )
            await uow.commit()
            return ManualPaymentDecision(
                PaymentView.model_validate(payment), client.telegram_id, True
            )

    async def get_manual_receipt(
        self,
        actor: StaffContext,
        payment_id: int,
    ) -> ManualReceiptAccess | None:
        """Reveal the Telegram file reference only after a fresh permission check."""

        live_actor = await self._authorization.authorize(
            business_id=actor.business_id,
            telegram_id=actor.telegram_id,
            permission=StaffPermission.VIEW_PREPAYMENTS,
        )
        async with self._unit_of_work_factory() as uow:
            if uow.business_id != live_actor.business_id:
                raise RuntimeError("payment unit of work tenant mismatch")
            payment = await uow.payments.get(payment_id)
            if payment is None:
                raise EntityNotFoundError("Платёж не найден.")
            appointment = await uow.appointments.get(payment.appointment_id)
            if appointment is None:
                raise EntityNotFoundError("Связанная запись не найдена.")
            self._require_assigned_if_master(live_actor, appointment.staff_member_id)
            if payment.receipt_file_id is None or payment.receipt_media_type is None:
                return None
            return ManualReceiptAccess(payment.receipt_file_id, payment.receipt_media_type)

    @staticmethod
    def _require_assigned_if_master(actor: StaffContext, staff_member_id: int) -> None:
        if actor.role is StaffRole.MASTER and staff_member_id != actor.staff_member_id:
            raise EntityNotFoundError("Платёж не найден.")

    async def request_remaining_refund(
        self,
        actor: StaffContext,
        payment_id: int,
        *,
        correlation_id: str | None = None,
    ) -> RefundOutcome:
        live_actor = await self._authorization.authorize(
            business_id=actor.business_id,
            telegram_id=actor.telegram_id,
            permission=StaffPermission.REFUND_PAYMENTS,
        )
        async with self._unit_of_work_factory() as unit_of_work:
            if unit_of_work.business_id != live_actor.business_id:
                raise RuntimeError("payment unit of work tenant mismatch")
            payment = await unit_of_work.payments.get(payment_id)
            if payment is None:
                raise EntityNotFoundError("Платёж не найден.")
            remaining = payment.amount - payment.refunded_amount
            if (
                payment.status
                not in {
                    PaymentStatus.SUCCEEDED,
                    PaymentStatus.PARTIALLY_REFUNDED,
                }
                or remaining <= 0
            ):
                raise PaymentStateError("Для этого платежа возврат недоступен.")
            coordinator = self._refund_coordinators.get(payment.provider)
            if coordinator is None:
                raise PaymentStateError("Провайдер возврата не настроен.")
            values = RefundCreate(
                business_id=payment.business_id,
                payment_id=payment.id,
                amount=remaining,
                currency=payment.currency,
                idempotency_key=(
                    f"tg-refund:{payment.id}:{payment.refunded_amount:.2f}:{remaining:.2f}"
                ),
                reason_code="requested_by_business",
                requested_by_user_id=live_actor.user_id,
                correlation_id=correlation_id,
                safe_metadata={"appointment_id": str(payment.appointment_id)},
            )
        return await coordinator.create_and_submit(live_actor, values)

    async def approve_manual_refund(
        self,
        actor: StaffContext,
        refund_id: int,
        *,
        correlation_id: str | None = None,
    ) -> RefundView:
        coordinator = self._refund_coordinators.get(PaymentMode.MANUAL)
        if coordinator is None:
            raise PaymentStateError("Ручные возвраты не настроены.")
        refund = await coordinator.approve_manual_refund(
            actor,
            refund_id,
            correlation_id=correlation_id,
        )
        if refund.status is not RefundStatus.SUCCEEDED:
            raise PaymentStateError("Возврат не подтверждён.")
        return refund
