"""Reservation expiry reconciliation, transitions and payment confirmation guards."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.models.appointment import Appointment
from app.database.models.availability_window import AvailabilityWindow
from app.database.models.commerce import BookingReservation
from app.database.models.payment import Payment
from app.domain.enums import (
    AppointmentStatus,
    AvailabilityWindowStatus,
    PaymentMode,
    PaymentStatus,
    ReservationStatus,
)
from app.domain.payments import PaymentType
from app.domain.reservations import ReservationExpiryAction, ReservationStateError
from app.services.reservation_service import ReservationService

NOW = datetime(2026, 8, 10, 10, tzinfo=UTC)


def appointment(*, status: AppointmentStatus) -> Appointment:
    return Appointment(
        id=21,
        business_id=7,
        client_id=41,
        staff_member_id=5,
        window_id=11,
        service_id=3,
        status=status,
        reservation_expires_at=NOW - timedelta(seconds=1),
    )


def reservation() -> BookingReservation:
    return BookingReservation(
        id=31,
        business_id=7,
        client_id=41,
        staff_member_id=5,
        window_id=11,
        service_id=3,
        appointment_id=21,
        token_digest="a" * 64,
        idempotency_key="request-12345678",
        status=ReservationStatus.ACTIVE,
        expires_at=NOW - timedelta(seconds=1),
        correlation_id="correlation-1234",
    )


def window() -> AvailabilityWindow:
    return AvailabilityWindow(
        id=11,
        business_id=7,
        staff_member_id=5,
        status=AvailabilityWindowStatus.RESERVED,
        start_at=NOW + timedelta(days=1),
        end_at=NOW + timedelta(days=1, hours=1),
        created_by=1,
    )


def payment(*, status: PaymentStatus) -> Payment:
    return Payment(
        id=51,
        business_id=7,
        appointment_id=21,
        provider=PaymentMode.YOOKASSA,
        idempotency_key="payment-12345678",
        amount=500,
        refunded_amount=0,
        currency="RUB",
        status=status,
        payment_type=PaymentType.DEPOSIT,
    )


def build_service(
    raw_appointment: Appointment,
    raw_window: AvailabilityWindow,
    latest_payment: Payment | None,
) -> tuple[ReservationService, MagicMock, MagicMock, MagicMock]:
    reservations = MagicMock()
    reservations.business_id = 7
    reservations.get_window_for_update = AsyncMock(return_value=raw_window)
    reservations.get_appointment_for_update = AsyncMock(return_value=raw_appointment)
    reservations.get = AsyncMock()
    reservations.add_history = AsyncMock()
    payments = MagicMock()
    payments.business_id = 7
    payments.get_latest_for_appointment = AsyncMock(return_value=latest_payment)
    audit = MagicMock()
    audit.business_id = 7
    audit.add = AsyncMock()
    return ReservationService(reservations, payments, audit), reservations, payments, audit


@pytest.mark.asyncio
async def test_unpaid_expiry_transitions_history_releases_window_and_audits() -> None:
    raw_appointment = appointment(status=AppointmentStatus.PENDING_PAYMENT)
    raw_window = window()
    raw_reservation = reservation()
    service, repository, _, audit = build_service(raw_appointment, raw_window, None)

    action = await service.expire_claimed(raw_reservation, now=NOW)

    assert action is ReservationExpiryAction.EXPIRED
    assert raw_appointment.status is AppointmentStatus.PAYMENT_EXPIRED
    assert raw_appointment.reservation_expires_at is None
    assert raw_reservation.status is ReservationStatus.EXPIRED
    assert raw_window.status is AvailabilityWindowStatus.OPEN
    history = repository.add_history.await_args.args[0]
    assert history.previous_status is AppointmentStatus.PENDING_PAYMENT
    assert history.new_status is AppointmentStatus.PAYMENT_EXPIRED
    audit.add.assert_awaited_once()


@pytest.mark.asyncio
async def test_paid_expiry_repairs_crash_window_instead_of_releasing_slot() -> None:
    raw_appointment = appointment(status=AppointmentStatus.PENDING_PAYMENT)
    raw_window = window()
    raw_reservation = reservation()
    service, repository, payments, audit = build_service(
        raw_appointment, raw_window, payment(status=PaymentStatus.SUCCEEDED)
    )

    action = await service.expire_claimed(raw_reservation, now=NOW)

    assert action is ReservationExpiryAction.RECONCILED
    assert raw_appointment.status is AppointmentStatus.CONFIRMED
    assert raw_reservation.status is ReservationStatus.CONSUMED
    assert raw_window.status is AvailabilityWindowStatus.BOOKED
    payments.get_latest_for_appointment.assert_awaited_once_with(21, for_update=True)
    repository.add_history.assert_awaited_once()
    assert audit.add.await_args.kwargs["action"] == "booking_reservation.reconciled_paid"


@pytest.mark.asyncio
async def test_consume_cannot_confirm_pending_payment_without_provider_success() -> None:
    raw_appointment = appointment(status=AppointmentStatus.PENDING_PAYMENT)
    raw_window = window()
    raw_reservation = reservation()
    raw_reservation.expires_at = NOW + timedelta(minutes=5)
    service, repository, _, audit = build_service(raw_appointment, raw_window, None)
    repository.get.return_value = raw_reservation

    with pytest.raises(ReservationStateError, match="Payment has not been confirmed"):
        await service.consume(
            31,
            appointment_id=21,
            actor_user_id=None,
            now=NOW,
        )

    assert raw_appointment.status is AppointmentStatus.PENDING_PAYMENT
    assert raw_reservation.status is ReservationStatus.ACTIVE
    audit.add.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirmed_appointment_is_reconciled_without_payment_lookup() -> None:
    raw_appointment = appointment(status=AppointmentStatus.CONFIRMED)
    raw_window = window()
    raw_reservation = reservation()
    service, _, payments, _ = build_service(raw_appointment, raw_window, None)

    action = await service.expire_claimed(raw_reservation, now=NOW)

    assert action is ReservationExpiryAction.RECONCILED
    payments.get_latest_for_appointment.assert_not_awaited()
