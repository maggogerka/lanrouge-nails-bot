"""Atomic reschedule state, lock and reminder tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.models import Appointment, AvailabilityWindow, BusinessSettings, User
from app.domain.enums import AppointmentStatus, AvailabilityWindowStatus
from app.domain.errors import BookingConflictError, CancellationDeadlineError
from app.schemas.booking import ClientActor
from app.services.reschedule_service import RescheduleService

NOW = datetime(2026, 7, 22, 9, tzinfo=UTC)


def settings() -> BusinessSettings:
    return BusinessSettings(
        id=1,
        business_name="lanrouge nails",
        timezone="Europe/Moscow",
        address="Новоостаповская, д. 20",
        map_url="https://yandex.ru/maps/-/CTbJz23i",
        master_telegram_url="https://t.me/lanrouge",
        booking_horizon_days=31,
        cancellation_deadline_hours=36,
        max_appointments_per_day=2,
        default_window_duration_minutes=210,
        minimum_gap_minutes=60,
        allow_saturday=False,
        allow_sunday=False,
        reminder_offsets_minutes=[1440, 180, 60],
        waitlist_notification_cooldown_minutes=180,
        version=1,
    )


def old_appointment() -> Appointment:
    return Appointment(
        id=11,
        client_id=5,
        window_id=7,
        service_id=3,
        service_name_snapshot="Маникюр",
        price_snapshot=Decimal("2500.00"),
        duration_min_snapshot=120,
        duration_max_snapshot=180,
        status=AppointmentStatus.CONFIRMED,
        client_comment="Сохранить",
    )


def windows(*, old_hours: int = 72) -> tuple[AvailabilityWindow, AvailabilityWindow]:
    old_start = NOW + timedelta(hours=old_hours)
    new_start = NOW + timedelta(days=5)
    return (
        AvailabilityWindow(
            id=7,
            start_at=old_start,
            end_at=old_start + timedelta(minutes=210),
            status=AvailabilityWindowStatus.BOOKED,
            created_by=9,
        ),
        AvailabilityWindow(
            id=8,
            start_at=new_start,
            end_at=new_start + timedelta(minutes=210),
            status=AvailabilityWindowStatus.OPEN,
            created_by=9,
        ),
    )


def build_uow(
    *,
    old_hours: int = 72,
    new_status: AvailabilityWindowStatus = AvailabilityWindowStatus.OPEN,
) -> tuple[MagicMock, Appointment, AvailabilityWindow, AvailabilityWindow]:
    appointment = old_appointment()
    old_window, new_window = windows(old_hours=old_hours)
    new_window.status = new_status
    client = User(
        id=5,
        telegram_id=101,
        first_name="Анна",
        phone="+79991234567",
    )
    unit_of_work = MagicMock()
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    unit_of_work.settings.get = AsyncMock(return_value=settings())
    unit_of_work.appointments.get = AsyncMock(return_value=appointment)
    unit_of_work.appointments.count_capacity_between = AsyncMock(return_value=0)

    async def add_appointment(new_appointment: Appointment) -> Appointment:
        new_appointment.id = 12
        return new_appointment

    unit_of_work.appointments.add = AsyncMock(side_effect=add_appointment)
    unit_of_work.appointments.add_history = AsyncMock()
    by_id = {7: old_window, 8: new_window}
    unit_of_work.windows.get = AsyncMock(side_effect=lambda window_id: by_id[window_id])
    unit_of_work.windows.get_many_for_update = AsyncMock(return_value=[old_window, new_window])
    unit_of_work.windows.lock_local_date = AsyncMock()
    unit_of_work.users.get_by_id = AsyncMock(return_value=client)
    unit_of_work.users.get_by_telegram_id = AsyncMock(return_value=client)
    unit_of_work.users.list_by_telegram_ids = AsyncMock(
        return_value=[SimpleNamespace(id=9, is_blocked=False)]
    )
    unit_of_work.notifications.cancel_unsent = AsyncMock(return_value=2)
    unit_of_work.notifications.add_all = AsyncMock()
    unit_of_work.reference_media.move_active = AsyncMock(return_value=2)
    unit_of_work.waitlist.list_matching = AsyncMock(return_value=[])
    unit_of_work.audit.add = AsyncMock()
    unit_of_work.commit = AsyncMock()
    return unit_of_work, appointment, old_window, new_window


@pytest.mark.asyncio
async def test_client_reschedule_inside_deadline_is_blocked() -> None:
    unit_of_work, _, _, _ = build_uow(old_hours=35)
    service = RescheduleService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    with pytest.raises(CancellationDeadlineError):
        await service.list_my_options(ClientActor(telegram_id=101), 11, now=NOW)


@pytest.mark.asyncio
async def test_reschedule_atomically_switches_windows_and_preserves_snapshot() -> None:
    unit_of_work, old, old_window, new_window = build_uow()
    service = RescheduleService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    receipt = await service.reschedule_my(
        ClientActor(telegram_id=101),
        11,
        8,
        now=NOW,
        correlation_id="request-2",
    )

    assert receipt.appointment_id == 12
    assert old.status is AppointmentStatus.RESCHEDULED
    assert old_window.status is AvailabilityWindowStatus.OPEN
    assert new_window.status is AvailabilityWindowStatus.BOOKED
    created = unit_of_work.appointments.add.await_args.args[0]
    assert created.rescheduled_from_id == 11
    assert created.price_snapshot == Decimal("2500.00")
    assert unit_of_work.appointments.add_history.await_count == 2
    unit_of_work.notifications.cancel_unsent.assert_awaited_once_with(11)
    assert unit_of_work.notifications.add_all.await_args.args[0]
    moved = unit_of_work.reference_media.move_active.await_args.args
    assert moved[0:2] == (11, 12)
    assert moved[2] > new_window.end_at
    locked_dates = [call.args[0] for call in unit_of_work.windows.lock_local_date.await_args_list]
    assert locked_dates == sorted(locked_dates)
    assert all(isinstance(item, date) for item in locked_dates)
    unit_of_work.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_reschedule_to_occupied_window_does_not_change_original() -> None:
    unit_of_work, old, old_window, _ = build_uow(new_status=AvailabilityWindowStatus.BOOKED)
    service = RescheduleService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    with pytest.raises(BookingConflictError):
        await service.reschedule_my(ClientActor(telegram_id=101), 11, 8, now=NOW)

    assert old.status is AppointmentStatus.CONFIRMED
    assert old_window.status is AvailabilityWindowStatus.BOOKED
    unit_of_work.appointments.add.assert_not_awaited()
    unit_of_work.commit.assert_not_awaited()
