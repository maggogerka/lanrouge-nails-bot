"""Availability use-case authorization and transaction tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.models import AvailabilityWindow, BusinessSettings, Service, Workstation
from app.domain.enums import AvailabilityWindowStatus, StaffRole
from app.domain.errors import AuthorizationError, WindowInUseError
from app.schemas.authorization import StaffContext
from app.schemas.availability import AvailabilityWindowCreate
from app.schemas.service import AdminActor
from app.services.availability_service import AvailabilityService

NOW = datetime(2026, 7, 22, 9, tzinfo=UTC)


def actor(telegram_id: int = 101) -> AdminActor:
    return AdminActor(telegram_id=telegram_id, username="admin", first_name="Admin")


def settings() -> BusinessSettings:
    return BusinessSettings(
        id=1,
        business_name="Example Studio",
        timezone="Europe/Moscow",
        address="Новоостаповская, д. 20",
        map_url="https://yandex.ru/maps/-/CTbJz23i",
        master_telegram_url="https://t.me/example_studio",
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


def build_uow() -> MagicMock:
    unit_of_work = MagicMock()
    unit_of_work.business_id = 1
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    unit_of_work.users.get_or_create_admin = AsyncMock(return_value=SimpleNamespace(id=5))
    unit_of_work.users.get_by_id = AsyncMock(return_value=SimpleNamespace(id=5))
    unit_of_work.settings.get = AsyncMock(return_value=settings())
    catalog_service = Service(
        id=1,
        business_id=1,
        name="Маникюр",
        price=Decimal("1000"),
        duration_min_minutes=60,
        duration_max_minutes=120,
        prepayment_amount=Decimal("0"),
        is_active=True,
        online_booking_enabled=True,
    )
    workstation = Workstation(id=1, business_id=1, name="Стол 1", is_active=True)
    unit_of_work.services.get = AsyncMock(return_value=catalog_service)
    unit_of_work.staff.get_by_id = AsyncMock(
        return_value=SimpleNamespace(
            id=1,
            display_name="Мастер",
            is_active=True,
            is_bookable=True,
        )
    )
    unit_of_work.service_assignments.get_assignment = AsyncMock(
        return_value=SimpleNamespace(is_active=True, online_booking_enabled=True)
    )
    unit_of_work.service_assignments.list_bookable_services_for_staff = AsyncMock(
        return_value=[(SimpleNamespace(), catalog_service)]
    )
    unit_of_work.workstations.lock_allocation_date = AsyncMock()
    unit_of_work.workstations.allocate_available = AsyncMock(return_value=workstation)
    unit_of_work.workstations.get = AsyncMock(return_value=workstation)
    unit_of_work.workstations.list_active_for_service = AsyncMock(return_value=[workstation])
    unit_of_work.workstations.has_available = AsyncMock(return_value=True)
    unit_of_work.windows.list_upcoming = AsyncMock(return_value=[])
    unit_of_work.windows.list_active_between = AsyncMock(return_value=[])
    unit_of_work.windows.lock_local_date = AsyncMock()
    unit_of_work.windows.get = AsyncMock(return_value=None)
    unit_of_work.windows.has_appointments = AsyncMock(return_value=False)
    unit_of_work.windows.delete = AsyncMock()
    unit_of_work.hard_delete.delete_window_with_history = AsyncMock(return_value=0)
    unit_of_work.audit.add = AsyncMock()
    unit_of_work.waitlist.list_matching = AsyncMock(return_value=[])
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


def master_actor(*, staff_member_id: int = 1) -> StaffContext:
    return StaffContext(
        business_id=1,
        staff_member_id=staff_member_id,
        user_id=5,
        telegram_id=202,
        display_name="Мастер",
        role=StaffRole.MASTER,
        is_bookable=True,
    )


def persisted_window(status: AvailabilityWindowStatus) -> AvailabilityWindow:
    return AvailabilityWindow(
        id=7,
        business_id=1,
        staff_member_id=1,
        service_id=1,
        workstation_id=1,
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
async def test_archived_window_visibility_is_forwarded_to_repository() -> None:
    unit_of_work = build_uow()
    service = AvailabilityService(lambda: unit_of_work, frozenset({101}))  # type: ignore[arg-type]

    await service.list_windows(actor(), include_archived=True, now=NOW)

    unit_of_work.windows.list_upcoming.assert_awaited_once_with(
        NOW,
        include_archived=True,
    )


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
    assert created.service_id is None
    assert created.workstation_id is None
    unit_of_work.windows.lock_local_date.assert_awaited_once_with(
        date(2026, 7, 23), staff_member_id=1
    )
    unit_of_work.windows.list_active_between.assert_awaited_once()
    audit = unit_of_work.audit.add.await_args.kwargs
    assert audit["action"] == "availability_window.created"
    assert audit["correlation_id"] == "request-1"
    assert audit["changes"]["after"]["has_admin_comment"] is True
    assert "do not expose" not in str(audit["changes"])
    unit_of_work.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_master_creates_only_own_free_window() -> None:
    unit_of_work = build_uow()

    async def add_window(window: AvailabilityWindow) -> AvailabilityWindow:
        window.id = 9
        return window

    unit_of_work.windows.add = AsyncMock(side_effect=add_window)
    service = AvailabilityService(lambda: unit_of_work, frozenset())  # type: ignore[arg-type]

    created = await service.create_window(master_actor(), create_values(), now=NOW)

    assert created.staff_member_id == 1
    assert created.service_id is None
    assert created.workstation_id is None
    unit_of_work.users.get_by_id.assert_awaited_once_with(5)
    unit_of_work.users.get_or_create_admin.assert_not_awaited()
    unit_of_work.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_master_cannot_create_window_for_another_staff_member() -> None:
    factory = MagicMock()
    service = AvailabilityService(factory, frozenset())
    values = create_values().model_copy(update={"staff_member_id": 2})

    with pytest.raises(AuthorizationError, match="только для себя"):
        await service.create_window(master_actor(), values, now=NOW)

    factory.assert_not_called()


@pytest.mark.asyncio
async def test_preview_validates_interval_without_writing_or_committing() -> None:
    unit_of_work = build_uow()
    service = AvailabilityService(lambda: unit_of_work, frozenset({101}))  # type: ignore[arg-type]

    preview = await service.preview_window(actor(), create_values(), now=NOW)

    assert preview.start_at == datetime(2026, 7, 23, 7, tzinfo=UTC)
    assert preview.end_at == datetime(2026, 7, 23, 10, 30, tzinfo=UTC)
    assert preview.duration_minutes == 210
    assert preview.master_name == "Мастер"
    unit_of_work.windows.lock_local_date.assert_awaited_once()
    unit_of_work.audit.add.assert_not_awaited()
    unit_of_work.commit.assert_not_awaited()


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


@pytest.mark.asyncio
async def test_owner_can_force_delete_window_aggregate() -> None:
    unit_of_work = build_uow()
    window = persisted_window(AvailabilityWindowStatus.BOOKED)
    unit_of_work.windows.get = AsyncMock(return_value=window)
    unit_of_work.hard_delete.delete_window_with_history = AsyncMock(return_value=2)
    service = AvailabilityService(lambda: unit_of_work, frozenset({101}))  # type: ignore[arg-type]

    deleted = await service.force_delete_window(actor(), 7, correlation_id="force-window")

    assert deleted == 2
    unit_of_work.hard_delete.delete_window_with_history.assert_awaited_once_with(7)
    assert unit_of_work.audit.add.await_args.kwargs["action"] == (
        "availability_window.force_deleted"
    )
    unit_of_work.commit.assert_awaited_once()
