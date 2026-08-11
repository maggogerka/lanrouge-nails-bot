"""Manual prepayment state machine and replay safety."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.models import Appointment, BookingReservation, Payment, User
from app.domain.enums import (
    AppointmentStatus,
    ManualPaymentStatus,
    PaymentMode,
    PaymentStatus,
    ReservationStatus,
)
from app.domain.payments import PaymentStateError, PaymentType
from app.schemas.booking import ClientActor
from app.schemas.payment import ManualReceiptDraft
from app.services.manual_prepayment_service import ManualPrepaymentService

NOW = datetime(2026, 8, 11, 9, tzinfo=UTC)


def actor() -> ClientActor:
    return ClientActor(telegram_id=700, username="client", first_name="Анна")


def entities(
    *, expires_at: datetime | None = None
) -> tuple[User, Appointment, Payment, BookingReservation]:
    user = User(id=4, telegram_id=700)
    appointment = Appointment(
        id=11,
        business_id=1,
        client_id=4,
        status=AppointmentStatus.PENDING_PAYMENT,
        reservation_expires_at=expires_at or NOW + timedelta(minutes=15),
    )
    payment = Payment(
        id=21,
        business_id=1,
        appointment_id=11,
        provider=PaymentMode.MANUAL,
        idempotency_key="manual-payment-00000001",
        amount=Decimal("500.00"),
        refunded_amount=Decimal("0.00"),
        currency="RUB",
        status=PaymentStatus.PENDING,
        payment_type=PaymentType.DEPOSIT,
        safe_metadata={},
        attempts=0,
        manual_status=ManualPaymentStatus.AWAITING_PAYMENT,
    )
    reservation = BookingReservation(
        id=31,
        business_id=1,
        client_id=4,
        staff_member_id=5,
        window_id=6,
        service_id=7,
        appointment_id=11,
        token_digest="a" * 64,
        idempotency_key="manual-reservation-0001",
        status=ReservationStatus.ACTIVE,
        expires_at=expires_at or NOW + timedelta(minutes=15),
    )
    return user, appointment, payment, reservation


def uow_for(
    user: User,
    appointment: Appointment,
    payment: Payment,
    reservation: BookingReservation,
) -> MagicMock:
    uow = MagicMock()
    uow.__aenter__ = AsyncMock(return_value=uow)
    uow.__aexit__ = AsyncMock(return_value=None)
    uow.users.get_by_telegram_id = AsyncMock(return_value=user)
    uow.payments.get = AsyncMock(return_value=payment)
    uow.appointments.get = AsyncMock(return_value=appointment)
    uow.reservations.get_active_for_appointment = AsyncMock(return_value=reservation)
    uow.reservations.payment_settings = AsyncMock(return_value=None)
    uow.appointments.add_history = AsyncMock()
    uow.staff.list_active_by_roles = AsyncMock(return_value=[])
    uow.notifications.add_all = AsyncMock()
    uow.audit.add = AsyncMock()
    uow.commit = AsyncMock()
    return uow


@pytest.mark.asyncio
async def test_report_paid_stops_expiry_and_is_idempotent() -> None:
    user, appointment, payment, reservation = entities()
    uow = uow_for(user, appointment, payment, reservation)
    service = ManualPrepaymentService(lambda: uow)

    first = await service.report_paid(actor(), 21, now=NOW, correlation_id="paid-1")
    replay = await service.report_paid(actor(), 21, now=NOW, correlation_id="paid-1")

    assert first.changed
    assert not replay.changed
    assert payment.manual_status is ManualPaymentStatus.CLIENT_REPORTED
    assert reservation.status is ReservationStatus.AWAITING_REVIEW
    assert appointment.status is AppointmentStatus.PENDING_MANUAL_CONFIRMATION
    assert appointment.reservation_expires_at is None
    assert uow.audit.add.await_count == 1


@pytest.mark.asyncio
async def test_report_paid_loses_exact_expiry_boundary_without_mutation() -> None:
    user, appointment, payment, reservation = entities(expires_at=NOW)
    uow = uow_for(user, appointment, payment, reservation)

    with pytest.raises(PaymentStateError, match="истёк"):
        await ManualPrepaymentService(lambda: uow).report_paid(actor(), 21, now=NOW)

    assert payment.manual_status is ManualPaymentStatus.AWAITING_PAYMENT
    assert reservation.status is ReservationStatus.ACTIVE
    uow.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_optional_receipt_is_single_and_bounded() -> None:
    user, appointment, payment, reservation = entities()
    payment.manual_status = ManualPaymentStatus.CLIENT_REPORTED
    reservation.status = ReservationStatus.AWAITING_REVIEW
    appointment.status = AppointmentStatus.PENDING_MANUAL_CONFIRMATION
    uow = uow_for(user, appointment, payment, reservation)
    service = ManualPrepaymentService(lambda: uow)
    receipt = ManualReceiptDraft(
        telegram_file_id="secret-file-id",
        telegram_file_unique_id="unique-file",
        media_type="photo",
        file_size=1024,
    )

    first = await service.submit_for_review(actor(), 21, receipt=receipt, now=NOW)
    replay = await service.submit_for_review(actor(), 21, receipt=receipt, now=NOW)

    assert first.changed
    assert not replay.changed
    assert payment.manual_status is ManualPaymentStatus.REVIEW_PENDING
    assert payment.receipt_file_id == "secret-file-id"
    assert first.payment.has_receipt
    assert "receipt_file_id" not in first.payment.model_dump()
