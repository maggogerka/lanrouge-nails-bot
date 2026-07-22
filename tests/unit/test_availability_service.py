"""Availability use-case authorization and transaction tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.models import AvailabilityWindow, BusinessSettings
from app.domain.enums import AvailabilityWindowStatus
from app.domain.errors import AuthorizationError, WindowInUseError
from app.schemas.availability import AvailabilityWindowCreate
from app.schemas.service import AdminActor
from app.services.availability_service import AvailabilityService

NOW = datetime(2026, 7, 22, 9, tzinfo=UTC)


def actor(telegram_id: int = 101) -> AdminActor:
    return AdminActor(telegram_id=telegram_id, username="admin", first_name="Admin")


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
        version=1,
    )


def build_uow() -> MagicMock:
    unit_of_work = MagicMock()
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    unit_of_work.users.get_or_create_admin = AsyncMock(return_value=SimpleNamespace(id=5))
    unit_of_work.settings.get = AsyncMock(return_value=settings())
    unit_of_work.windows.list_upcoming = AsyncMock(return_value=[])
    unit_of_work.windows.list_active_between = AsyncMock(return_value=[])
    unit_of_work.windows.lock_local_date = AsyncMock()
    unit_of_work.windows.get = AsyncMock(return_value=None)
    unit_of_work.windows.has_appointments = AsyncMock(return_value=False)
    unit_of_work.windows.delete = AsyncMock()
    unit_of_work.audit.add = AsyncMock()
    unit_of_work.session.flush = AsyncMock()
    unit_of_work.commit = AsyncMock()
    return unit_of_work


def create_values(
    *, status: AvailabilityWindowStatus = AvailabilityWindowStatus.OPEN
) -> AvailabilityWindowCreate:
    return AvailabilityWindowCreate(
        local_date=date(2026, 7, 23),
        local_start_time=time(10),
        admin_comment="do not expose this text",
        status=status,
    )


def persisted_window(status: AvailabilityWindowStatus) -> AvailabilityWindow:
    return AvailabilityWindow(
        id=7,
        start_at=datetime(2026, 7, 23, 7, tzinfo=UTC),
        end_at=datetime(2026, 7, 23, 10, 30, tzinfo=UTC),
        status=status,
        admin_comment=None,
        created_by=5,
    )


@pytest.mark.asyncio
async def test_non_admin_is_rejected_before_opening_uow() -> None:
    factory = MagicMock()
    service = AvailabilityService(factory, frozenset({101}))

    with pytest.raises(AuthorizationError):
        await service.list_windows(actor(202), now=NOW)

    factory.assert_not_called()


@pytest.mark.asyncio
async def test_create_open_window_locks_date_audits_and_commits() -> None:
    unit_of_work = build_uow()

    async def add_window(window: AvailabilityWindow) -> AvailabilityWindow:
        window.id = 7
        return window

    unit_of_work.windows.add = AsyncMock(side_effect=add_window)
    service = AvailabilityService(lambda: unit_of_work, frozenset({101}))  # type: ignore[arg-type]

    created = await service.create_window(
        actor(),
        create_values(),
        now=NOW,
        correlation_id="request-1",
    )

    assert created.start_at == datetime(2026, 7, 23, 7, tzinfo=UTC)
    assert created.end_at == datetime(2026, 7, 23, 10, 30, tzinfo=UTC)
    unit_of_work.windows.lock_local_date.assert_awaited_once_with(date(2026, 7, 23))
    unit_of_work.windows.list_active_between.assert_awaited_once()
    audit = unit_of_work.audit.add.await_args.kwargs
    assert audit["action"] == "availability_window.created"
    assert audit["correlation_id"] == "request-1"
    assert audit["changes"]["after"]["has_admin_comment"] is True
    assert "do not expose" not in str(audit["changes"])
    unit_of_work.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_closed_window_does_not_consume_active_capacity() -> None:
    unit_of_work = build_uow()

    async def add_window(window: AvailabilityWindow) -> AvailabilityWindow:
        window.id = 8
        return window

    unit_of_work.windows.add = AsyncMock(side_effect=add_window)
    service = AvailabilityService(lambda: unit_of_work, frozenset({101}))  # type: ignore[arg-type]

    await service.create_window(
        actor(),
        create_values(status=AvailabilityWindowStatus.CLOSED),
        now=NOW,
    )

    unit_of_work.windows.lock_local_date.assert_not_awaited()
    unit_of_work.windows.list_active_between.assert_not_awaited()
    unit_of_work.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_close_open_window_changes_status_and_writes_audit() -> None:
    unit_of_work = build_uow()
    window = persisted_window(AvailabilityWindowStatus.OPEN)
    unit_of_work.windows.get = AsyncMock(return_value=window)
    service = AvailabilityService(lambda: unit_of_work, frozenset({101}))  # type: ignore[arg-type]

    closed = await service.close_window(actor(), 7, correlation_id="request-2")

    assert closed.status is AvailabilityWindowStatus.CLOSED
    unit_of_work.windows.get.assert_awaited_once_with(7, for_update=True)
    assert unit_of_work.audit.add.await_args.kwargs["action"] == (
        "availability_window.status_changed"
    )
    unit_of_work.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_window_with_appointment_history_cannot_be_deleted() -> None:
    unit_of_work = build_uow()
    unit_of_work.windows.get = AsyncMock(
        return_value=persisted_window(AvailabilityWindowStatus.CLOSED)
    )
    unit_of_work.windows.has_appointments = AsyncMock(return_value=True)
    service = AvailabilityService(lambda: unit_of_work, frozenset({101}))  # type: ignore[arg-type]

    with pytest.raises(WindowInUseError, match="история записей"):
        await service.delete_unused_window(actor(), 7)

    unit_of_work.windows.delete.assert_not_awaited()
    unit_of_work.commit.assert_not_awaited()
