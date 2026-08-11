"""Client-owned transitions for manual prepayments and optional receipt references."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.database.models import (
    Appointment,
    AppointmentStatusHistory,
    BookingReservation,
    NotificationJob,
    Payment,
    User,
)
from app.domain.appointments import ensure_appointment_transition
from app.domain.enums import (
    AppointmentStatus,
    ManualPaymentStatus,
    NotificationType,
    PaymentMode,
    PaymentStatus,
    ReservationStatus,
    StaffRole,
)
from app.domain.errors import AuthorizationError, EntityNotFoundError
from app.domain.payments import PaymentStateError, aware_utc
from app.domain.reservations import ensure_reservation_transition
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.authorization import StaffPermission, permissions_for_role
from app.schemas.booking import ClientActor
from app.schemas.payment import ManualReceiptDraft, PaymentView
from app.security import LEGACY_ADMIN_ROLES

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]
_RECEIPT_RETENTION_DAYS = 90


@dataclass(frozen=True, slots=True)
class ManualPrepaymentOutcome:
    payment: PaymentView
    changed: bool


class ManualPrepaymentService:
    """Serialize client reports against expiry and duplicate callbacks."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    async def report_paid(
        self,
        actor: ClientActor,
        payment_id: int,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ManualPrepaymentOutcome:
        current = aware_utc(now)
        async with self._unit_of_work_factory() as uow:
            user, payment, appointment, reservation = await self._locked_owned_payment(
                uow, actor, payment_id
            )
            if payment.manual_status in {
                ManualPaymentStatus.CLIENT_REPORTED,
                ManualPaymentStatus.REVIEW_PENDING,
            }:
                return ManualPrepaymentOutcome(PaymentView.model_validate(payment), False)
            if (
                payment.manual_status is not ManualPaymentStatus.AWAITING_PAYMENT
                or payment.status is not PaymentStatus.PENDING
                or reservation.status is not ReservationStatus.ACTIVE
            ):
                raise PaymentStateError("Эта заявка на предоплату уже завершена.")
            if reservation.expires_at <= current:
                raise PaymentStateError("Срок оплаты уже истёк. Обновите список записей.")

            ensure_reservation_transition(reservation.status, ReservationStatus.AWAITING_REVIEW)
            reservation.status = ReservationStatus.AWAITING_REVIEW
            appointment.reservation_expires_at = None
            if appointment.status is AppointmentStatus.PENDING_PAYMENT:
                ensure_appointment_transition(
                    appointment.status, AppointmentStatus.PENDING_MANUAL_CONFIRMATION
                )
                previous = appointment.status
                appointment.status = AppointmentStatus.PENDING_MANUAL_CONFIRMATION
                await uow.appointments.add_history(
                    AppointmentStatusHistory(
                        appointment_id=appointment.id,
                        previous_status=previous,
                        new_status=appointment.status,
                        changed_by_user_id=user.id,
                        reason="client_reported_manual_payment",
                    )
                )
            elif appointment.status is not AppointmentStatus.PENDING_MANUAL_CONFIRMATION:
                raise PaymentStateError("Запись уже не ожидает ручную предоплату.")
            payment.manual_status = ManualPaymentStatus.CLIENT_REPORTED
            payment.client_reported_at = current
            settings = await uow.reservations.payment_settings()
            if settings is not None and getattr(
                settings, "staff_payment_notifications_enabled", True
            ):
                offsets = sorted(
                    {
                        int(offset)
                        for offset in (
                            getattr(settings, "staff_review_reminder_minutes", None) or [30, 120]
                        )
                        if isinstance(offset, int) and 0 < offset <= 7 * 24 * 60
                    }
                )
                staff_rows = await uow.staff.list_active_by_roles(
                    uow.business_id, LEGACY_ADMIN_ROLES | {StaffRole.MASTER}
                )
                await uow.notifications.add_all(
                    [
                        NotificationJob(
                            business_id=uow.business_id,
                            appointment_id=appointment.id,
                            recipient_user_id=staff_user.id,
                            notification_type=NotificationType.PAYMENT_REVIEW_STAFF,
                            offset_minutes=offset,
                            scheduled_at=current + timedelta(minutes=offset),
                            available_at=current + timedelta(minutes=offset),
                        )
                        for staff_member, staff_user in staff_rows
                        if not staff_user.is_blocked
                        and (
                            staff_member.role is not StaffRole.MASTER
                            or staff_member.id == appointment.staff_member_id
                        )
                        and StaffPermission.VIEW_PREPAYMENTS
                        in (
                            permissions_for_role(staff_member.role)
                            | {
                                StaffPermission(value)
                                for value in (staff_member.permission_grants or [])
                            }
                        )
                        for offset in offsets
                    ]
                )
            await uow.audit.add(
                actor_user_id=user.id,
                action="payment.manual_client_reported",
                entity_type="payment",
                entity_id=str(payment.id),
                changes={"appointment_id": appointment.id, "has_receipt": False},
                correlation_id=correlation_id,
            )
            await uow.commit()
            return ManualPrepaymentOutcome(PaymentView.model_validate(payment), True)

    async def submit_for_review(
        self,
        actor: ClientActor,
        payment_id: int,
        *,
        receipt: ManualReceiptDraft | None,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ManualPrepaymentOutcome:
        current = aware_utc(now)
        async with self._unit_of_work_factory() as uow:
            user, payment, _appointment, reservation = await self._locked_owned_payment(
                uow, actor, payment_id
            )
            if payment.manual_status is ManualPaymentStatus.REVIEW_PENDING:
                if receipt is not None and (
                    payment.receipt_file_unique_id != receipt.telegram_file_unique_id
                ):
                    raise PaymentStateError("Чек уже был отправлен для этой предоплаты.")
                return ManualPrepaymentOutcome(PaymentView.model_validate(payment), False)
            if (
                payment.manual_status is not ManualPaymentStatus.CLIENT_REPORTED
                or reservation.status is not ReservationStatus.AWAITING_REVIEW
            ):
                raise PaymentStateError("Сначала нажмите «Я оплатил».")
            if receipt is not None:
                payment.receipt_file_id = receipt.telegram_file_id
                payment.receipt_file_unique_id = receipt.telegram_file_unique_id
                payment.receipt_media_type = receipt.media_type
                payment.receipt_file_size = receipt.file_size
                payment.receipt_received_at = current
                payment.receipt_expires_at = current + timedelta(days=_RECEIPT_RETENTION_DAYS)
            payment.manual_status = ManualPaymentStatus.REVIEW_PENDING
            payment.review_started_at = current
            await uow.audit.add(
                actor_user_id=user.id,
                action="payment.manual_review_requested",
                entity_type="payment",
                entity_id=str(payment.id),
                changes={
                    "appointment_id": payment.appointment_id,
                    "has_receipt": receipt is not None,
                },
                correlation_id=correlation_id,
            )
            await uow.commit()
            return ManualPrepaymentOutcome(PaymentView.model_validate(payment), True)

    @staticmethod
    async def _locked_owned_payment(
        uow: SqlAlchemyUnitOfWork,
        actor: ClientActor,
        payment_id: int,
    ) -> tuple[User, Payment, Appointment, BookingReservation]:
        user = await uow.users.get_by_telegram_id(actor.telegram_id)
        payment_hint = await uow.payments.get(payment_id)
        if user is None or payment_hint is None:
            raise EntityNotFoundError("Предоплата не найдена.")
        appointment_hint = await uow.appointments.get(payment_hint.appointment_id)
        if appointment_hint is None or appointment_hint.client_id != user.id:
            raise AuthorizationError("Эта предоплата вам недоступна.")
        reservation = await uow.reservations.get_active_for_appointment(
            appointment_hint.id, for_update=True
        )
        payment = await uow.payments.get(payment_id, for_update=True)
        appointment = await uow.appointments.get(appointment_hint.id, for_update=True)
        if payment is None or appointment is None or reservation is None:
            raise PaymentStateError("Резерв этой предоплаты уже завершён.")
        if (
            payment.provider is not PaymentMode.MANUAL
            or payment.appointment_id != appointment.id
            or appointment.client_id != user.id
            or reservation.appointment_id != appointment.id
        ):
            raise AuthorizationError("Эта предоплата вам недоступна.")
        return user, payment, appointment, reservation
