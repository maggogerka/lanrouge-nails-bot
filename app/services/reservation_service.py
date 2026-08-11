"""Transactional reservation lifecycle shared by checkout and expiry workers."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.database.models.appointment import Appointment, AppointmentStatusHistory
from app.database.models.availability_window import AvailabilityWindow
from app.database.models.commerce import BookingReservation
from app.domain.appointments import ensure_appointment_transition
from app.domain.enums import (
    AppointmentStatus,
    AvailabilityWindowStatus,
    ManualPaymentStatus,
    PaymentStatus,
    ReservationStatus,
)
from app.domain.payments import aware_utc
from app.domain.reservations import (
    ReservationExpiryAction,
    ReservationStateError,
    ReservationToken,
    ensure_reservation_transition,
)
from app.repositories.audit_repository import AuditRepository
from app.repositories.payment_repository import PaymentRepository
from app.repositories.reservation_repository import ReservationRepository
from app.schemas.reservation import ReservationCreate

_PENDING_APPOINTMENT_STATUSES = {
    AppointmentStatus.PENDING_PAYMENT,
    AppointmentStatus.PENDING_MANUAL_CONFIRMATION,
}
_CONFIRMED_APPOINTMENT_STATUSES = {
    AppointmentStatus.CONFIRMED,
    AppointmentStatus.CLIENT_CONFIRMED,
}
_WINDOW_RELEASE_APPOINTMENT_STATUSES = {
    AppointmentStatus.PAYMENT_EXPIRED,
    AppointmentStatus.CANCELLED_BY_CLIENT,
    AppointmentStatus.CANCELLED_BY_ADMIN,
    AppointmentStatus.RESCHEDULED,
}


class ReservationService:
    """Mutate rows locked by repositories; transaction ownership remains with the caller."""

    def __init__(
        self,
        reservations: ReservationRepository,
        payments: PaymentRepository,
        audit: AuditRepository,
    ) -> None:
        business_ids = {reservations.business_id, payments.business_id, audit.business_id}
        if len(business_ids) != 1:
            raise ValueError("reservation dependencies must share one business scope")
        self._business_id = reservations.business_id
        self._reservations = reservations
        self._payments = payments
        self._audit = audit

    async def create(
        self,
        values: ReservationCreate,
        token: ReservationToken,
        *,
        now: datetime | None = None,
    ) -> tuple[BookingReservation, bool]:
        """Create one hold after locking its window; replay returns the original row."""

        current = aware_utc(now)
        self._require_business(values.business_id)
        existing = await self._reservations.get_by_idempotency_key(
            values.idempotency_key, for_update=True
        )
        if existing is not None:
            self._require_same_reservation(existing, values, token)
            return existing, False

        window = await self._required_window(values.window_id)
        if window.staff_member_id != values.staff_member_id:
            raise ReservationStateError("Окно относится к другому мастеру.")
        if window.status is not AvailabilityWindowStatus.OPEN:
            raise ReservationStateError("Выбранное время уже недоступно.")
        active = await self._reservations.get_active_for_window(values.window_id, for_update=True)
        if active is not None:
            raise ReservationStateError("Выбранное время уже зарезервировано.")

        appointment: Appointment | None = None
        if values.appointment_id is not None:
            appointment = await self._required_appointment(values.appointment_id)
            self._require_appointment_matches(appointment, values)
            if appointment.status not in _PENDING_APPOINTMENT_STATUSES:
                raise ReservationStateError("Запись уже не ожидает оплату или подтверждение.")

        expires_at = current + timedelta(minutes=values.ttl_minutes)
        reservation = BookingReservation(
            business_id=values.business_id,
            client_id=values.client_id,
            staff_member_id=values.staff_member_id,
            window_id=values.window_id,
            service_id=values.service_id,
            appointment_id=values.appointment_id,
            token_digest=token.digest,
            idempotency_key=values.idempotency_key,
            status=ReservationStatus.ACTIVE,
            expires_at=expires_at,
            correlation_id=values.correlation_id,
        )
        window.status = AvailabilityWindowStatus.RESERVED
        if appointment is not None:
            appointment.reservation_expires_at = expires_at
        await self._reservations.add(reservation)
        await self._audit.add(
            actor_user_id=values.client_id,
            action="booking_reservation.created",
            entity_type="booking_reservation",
            entity_id=str(reservation.id),
            changes={
                "window_id": values.window_id,
                "appointment_id": values.appointment_id,
                "status": ReservationStatus.ACTIVE.value,
                "expires_at": expires_at.isoformat(),
            },
            correlation_id=values.correlation_id,
        )
        return reservation, True

    async def consume(
        self,
        reservation_id: int,
        *,
        appointment_id: int,
        actor_user_id: int | None,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> BookingReservation:
        """Confirm a paid/manual-approved pending appointment and consume its hold."""

        current = aware_utc(now)
        reservation = await self._required_reservation(reservation_id)
        if reservation.status is ReservationStatus.CONSUMED:
            if reservation.appointment_id == appointment_id:
                return reservation
            raise ReservationStateError("Резерв уже связан с другой записью.")
        ensure_reservation_transition(reservation.status, ReservationStatus.CONSUMED)
        if reservation.status is ReservationStatus.ACTIVE and reservation.expires_at <= current:
            raise ReservationStateError("Срок резерва уже истёк.")

        appointment = await self._required_appointment(appointment_id)
        self._require_reservation_matches_appointment(reservation, appointment)
        if appointment.status is AppointmentStatus.PENDING_PAYMENT:
            payment = await self._payments.get_latest_for_appointment(
                appointment.id, for_update=True
            )
            if payment is None or payment.status is not PaymentStatus.SUCCEEDED:
                raise ReservationStateError("Payment has not been confirmed by the provider.")
        if appointment.status in _PENDING_APPOINTMENT_STATUSES:
            await self._transition_appointment(
                appointment,
                AppointmentStatus.CONFIRMED,
                changed_by_user_id=actor_user_id,
                reason="reservation_consumed",
            )
        elif appointment.status not in _CONFIRMED_APPOINTMENT_STATUSES:
            raise ReservationStateError("Запись уже не может быть подтверждена.")

        window = await self._required_window(reservation.window_id)
        if window.status not in {
            AvailabilityWindowStatus.RESERVED,
            AvailabilityWindowStatus.BOOKED,
        }:
            raise ReservationStateError("Состояние окна не соответствует активному резерву.")
        reservation.appointment_id = appointment.id
        reservation.status = ReservationStatus.CONSUMED
        reservation.consumed_at = current
        appointment.reservation_expires_at = None
        window.status = AvailabilityWindowStatus.BOOKED
        await self._audit.add(
            actor_user_id=actor_user_id,
            action="booking_reservation.consumed",
            entity_type="booking_reservation",
            entity_id=str(reservation.id),
            changes={
                "appointment_id": appointment.id,
                "status": ReservationStatus.CONSUMED.value,
            },
            correlation_id=correlation_id or reservation.correlation_id,
        )
        return reservation

    async def expire_claimed(
        self,
        reservation: BookingReservation,
        *,
        now: datetime | None = None,
    ) -> ReservationExpiryAction:
        """Expire one SKIP LOCKED row or repair a paid/confirmed crash window."""

        current = aware_utc(now)
        self._require_business(reservation.business_id)
        if reservation.status is not ReservationStatus.ACTIVE:
            raise ReservationStateError("Резерв больше не активен.")
        if reservation.expires_at > current:
            raise ReservationStateError("Резерв ещё не истёк.")
        window = await self._required_window(reservation.window_id)
        appointment: Appointment | None = None
        if reservation.appointment_id is not None:
            appointment = await self._required_appointment(reservation.appointment_id)
            self._require_reservation_matches_appointment(reservation, appointment)

        if appointment is not None and await self._must_reconcile_as_paid(appointment):
            if appointment.status in _PENDING_APPOINTMENT_STATUSES:
                await self._transition_appointment(
                    appointment,
                    AppointmentStatus.CONFIRMED,
                    changed_by_user_id=None,
                    reason="reservation_expiry_paid_reconciliation",
                )
            reservation.status = ReservationStatus.CONSUMED
            reservation.consumed_at = current
            appointment.reservation_expires_at = None
            window.status = AvailabilityWindowStatus.BOOKED
            await self._audit.add(
                actor_user_id=None,
                action="booking_reservation.reconciled_paid",
                entity_type="booking_reservation",
                entity_id=str(reservation.id),
                changes={
                    "appointment_id": appointment.id,
                    "status": ReservationStatus.CONSUMED.value,
                },
                correlation_id=reservation.correlation_id,
            )
            return ReservationExpiryAction.RECONCILED

        if appointment is not None and appointment.status in _PENDING_APPOINTMENT_STATUSES:
            await self._transition_appointment(
                appointment,
                AppointmentStatus.PAYMENT_EXPIRED,
                changed_by_user_id=None,
                reason="reservation_expired",
            )
            appointment.reservation_expires_at = None
            payment = await self._payments.get_latest_for_appointment(
                appointment.id, for_update=True
            )
            if payment is not None and payment.status is PaymentStatus.PENDING:
                payment.status = PaymentStatus.CANCELLED
                payment.cancelled_at = current
                if payment.manual_status is ManualPaymentStatus.AWAITING_PAYMENT:
                    payment.manual_status = ManualPaymentStatus.EXPIRED

        ensure_reservation_transition(reservation.status, ReservationStatus.EXPIRED)
        reservation.status = ReservationStatus.EXPIRED
        reservation.cancelled_at = current
        if self._window_can_be_released(appointment) and window.status in {
            AvailabilityWindowStatus.RESERVED,
            AvailabilityWindowStatus.BOOKED,
        }:
            window.status = AvailabilityWindowStatus.OPEN
        await self._audit.add(
            actor_user_id=None,
            action="booking_reservation.expired",
            entity_type="booking_reservation",
            entity_id=str(reservation.id),
            changes={
                "appointment_id": reservation.appointment_id,
                "status": ReservationStatus.EXPIRED.value,
                "window_status": window.status.value,
            },
            correlation_id=reservation.correlation_id,
        )
        return ReservationExpiryAction.EXPIRED

    async def _must_reconcile_as_paid(self, appointment: Appointment) -> bool:
        if appointment.status in _CONFIRMED_APPOINTMENT_STATUSES:
            return True
        if appointment.status not in _PENDING_APPOINTMENT_STATUSES:
            return False
        payment = await self._payments.get_latest_for_appointment(appointment.id, for_update=True)
        return payment is not None and payment.status is PaymentStatus.SUCCEEDED

    async def _transition_appointment(
        self,
        appointment: Appointment,
        target: AppointmentStatus,
        *,
        changed_by_user_id: int | None,
        reason: str,
    ) -> None:
        previous = appointment.status
        ensure_appointment_transition(previous, target)
        appointment.status = target
        await self._reservations.add_history(
            AppointmentStatusHistory(
                appointment_id=appointment.id,
                previous_status=previous,
                new_status=target,
                changed_by_user_id=changed_by_user_id,
                reason=reason,
            )
        )

    async def _required_reservation(self, reservation_id: int) -> BookingReservation:
        reservation = await self._reservations.get(reservation_id, for_update=True)
        if reservation is None:
            raise ReservationStateError("Резерв не найден.")
        return reservation

    async def _required_window(self, window_id: int) -> AvailabilityWindow:
        window = await self._reservations.get_window_for_update(window_id)
        if window is None:
            raise ReservationStateError("Окно не найдено в этом бизнесе.")
        return window

    async def _required_appointment(self, appointment_id: int) -> Appointment:
        appointment = await self._reservations.get_appointment_for_update(appointment_id)
        if appointment is None:
            raise ReservationStateError("Запись не найдена в этом бизнесе.")
        return appointment

    def _require_business(self, business_id: int) -> None:
        if business_id != self._business_id:
            raise ReservationStateError("Объект относится к другому бизнесу.")

    @staticmethod
    def _require_same_reservation(
        reservation: BookingReservation,
        values: ReservationCreate,
        token: ReservationToken,
    ) -> None:
        actual = (
            reservation.client_id,
            reservation.staff_member_id,
            reservation.window_id,
            reservation.service_id,
            reservation.appointment_id,
            reservation.token_digest,
        )
        expected = (
            values.client_id,
            values.staff_member_id,
            values.window_id,
            values.service_id,
            values.appointment_id,
            token.digest,
        )
        if actual != expected:
            raise ReservationStateError("Ключ идемпотентности уже связан с другим резервом.")

    @staticmethod
    def _require_appointment_matches(appointment: Appointment, values: ReservationCreate) -> None:
        if (
            appointment.business_id != values.business_id
            or appointment.client_id != values.client_id
            or appointment.staff_member_id != values.staff_member_id
            or appointment.window_id != values.window_id
            or appointment.service_id != values.service_id
        ):
            raise ReservationStateError("Резерв не соответствует записи.")

    @staticmethod
    def _require_reservation_matches_appointment(
        reservation: BookingReservation, appointment: Appointment
    ) -> None:
        if (
            reservation.business_id != appointment.business_id
            or reservation.client_id != appointment.client_id
            or reservation.staff_member_id != appointment.staff_member_id
            or reservation.window_id != appointment.window_id
            or reservation.service_id != appointment.service_id
        ):
            raise ReservationStateError("Резерв не соответствует записи.")

    @staticmethod
    def _window_can_be_released(appointment: Appointment | None) -> bool:
        return appointment is None or appointment.status in _WINDOW_RELEASE_APPOINTMENT_STATUSES
