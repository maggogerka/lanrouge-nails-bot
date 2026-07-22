"""Authorized, versioned business settings mutation tests."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.database.models import Appointment, AvailabilityWindow, NotificationJob, User
from app.domain.enums import (
    AppointmentStatus,
    AvailabilityWindowStatus,
    NotificationJobStatus,
    NotificationType,
)
from app.schemas.service import AdminActor
from app.schemas.settings import BusinessSettingsPatch
from app.services.settings_service import SettingsService
from tests.unit.test_appointment_service import settings

NOW = datetime(2026, 7, 22, 9, tzinfo=UTC)


def build_uow() -> MagicMock:
    unit_of_work = MagicMock()
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    unit_of_work.users.get_or_create_admin = AsyncMock(return_value=SimpleNamespace(id=9))
    unit_of_work.settings.get = AsyncMock(return_value=settings())
    unit_of_work.session.flush = AsyncMock()
    unit_of_work.audit.add = AsyncMock()
    unit_of_work.commit = AsyncMock()
    return unit_of_work


def test_reminder_offsets_must_be_unique_and_non_empty() -> None:
    with pytest.raises(ValidationError, match="unique"):
        BusinessSettingsPatch(reminder_offsets_minutes=[60, 60])


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("portfolio_max_media", 11),
        ("broadcast_messages_per_second", 21),
        ("repeat_booking_reminder_days", 0),
        ("client_page_size", 51),
        ("availability_date_picker_days", 63),
        ("booking_reference_max_media", 11),
    ],
)
def test_v020_settings_enforce_documented_bounds(field: str, value: int) -> None:
    with pytest.raises(ValidationError):
        BusinessSettingsPatch.model_validate({field: value})


def test_v030_time_step_and_external_url_are_validated() -> None:
    with pytest.raises(ValidationError, match="divide"):
        BusinessSettingsPatch(availability_time_step_minutes=61)
    with pytest.raises(ValidationError, match="HTTPS"):
        BusinessSettingsPatch(external_portfolio_url="http://example.com/portfolio")

    assert (
        BusinessSettingsPatch(
            external_portfolio_url="https://example.com/portfolio"
        ).external_portfolio_url
        == "https://example.com/portfolio"
    )


@pytest.mark.asyncio
async def test_setting_update_locks_row_increments_version_and_audits() -> None:
    unit_of_work = build_uow()
    service = SettingsService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    updated = await service.update(
        AdminActor(telegram_id=900),
        BusinessSettingsPatch(max_appointments_per_day=3),
        correlation_id="request-3",
    )

    assert updated.max_appointments_per_day == 3
    assert updated.version == 2
    unit_of_work.settings.get.assert_awaited_once_with(for_update=True)
    assert unit_of_work.audit.add.await_args.kwargs["changes"]["max_appointments_per_day"] == {
        "before": 2,
        "after": 3,
    }
    unit_of_work.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_reminder_offset_update_rebuilds_only_unsent_future_jobs() -> None:
    unit_of_work = build_uow()
    client = User(id=5, telegram_id=101, is_blocked=False)
    appointment = Appointment(
        id=11,
        client_id=5,
        window_id=7,
        service_id=3,
        service_name_snapshot="Маникюр",
        price_snapshot=Decimal("2500.00"),
        duration_min_snapshot=120,
        duration_max_snapshot=180,
        status=AppointmentStatus.CONFIRMED,
    )
    window = AvailabilityWindow(
        id=7,
        start_at=NOW + timedelta(hours=5),
        end_at=NOW + timedelta(hours=8),
        status=AvailabilityWindowStatus.BOOKED,
        created_by=9,
    )
    sent = NotificationJob(
        id=1,
        appointment_id=11,
        recipient_user_id=5,
        notification_type=NotificationType.CLIENT_REMINDER,
        offset_minutes=1440,
        scheduled_at=NOW - timedelta(hours=19),
        available_at=NOW - timedelta(hours=19),
        status=NotificationJobStatus.SENT,
        attempts=1,
    )
    obsolete = NotificationJob(
        id=2,
        appointment_id=11,
        recipient_user_id=5,
        notification_type=NotificationType.CLIENT_REMINDER,
        offset_minutes=180,
        scheduled_at=NOW + timedelta(hours=2),
        available_at=NOW + timedelta(hours=2),
        status=NotificationJobStatus.PENDING,
        attempts=0,
    )
    rearmed = NotificationJob(
        id=3,
        appointment_id=11,
        recipient_user_id=5,
        notification_type=NotificationType.CLIENT_REMINDER,
        offset_minutes=60,
        scheduled_at=NOW + timedelta(hours=4),
        available_at=NOW + timedelta(hours=4),
        status=NotificationJobStatus.FAILED,
        attempts=5,
    )
    unit_of_work.users.list_by_telegram_ids = AsyncMock(
        return_value=[SimpleNamespace(id=9, is_blocked=False)]
    )
    unit_of_work.users.get_by_id = AsyncMock(return_value=client)
    unit_of_work.appointments.list_future_active = AsyncMock(return_value=[(appointment, window)])
    unit_of_work.notifications.list_for_appointment = AsyncMock(
        return_value=[sent, obsolete, rearmed]
    )
    unit_of_work.notifications.add_all = AsyncMock()
    service = SettingsService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    await service.update(
        AdminActor(telegram_id=900),
        BusinessSettingsPatch(reminder_offsets_minutes=[60, 30]),
        now=NOW,
    )

    assert sent.status is NotificationJobStatus.SENT
    assert obsolete.status is NotificationJobStatus.CANCELLED
    assert rearmed.status is NotificationJobStatus.PENDING
    assert rearmed.attempts == 0
    new_jobs = unit_of_work.notifications.add_all.await_args.args[0]
    assert len(new_jobs) == 3
    assert {(job.recipient_user_id, job.offset_minutes) for job in new_jobs} == {
        (9, 60),
        (5, 30),
        (9, 30),
    }
