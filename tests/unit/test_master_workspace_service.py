"""Master workspace remains self-scoped and revalidates live membership."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.enums import (
    AppointmentStatus,
    AvailabilityWindowStatus,
    ScheduleIntervalKind,
    StaffRole,
)
from app.domain.errors import AppointmentStateError, AuthorizationError
from app.schemas.authorization import StaffContext
from app.services.master_workspace_service import MasterWorkspaceService

NOW = datetime(2026, 8, 10, 9, tzinfo=UTC)


def actor(*, business_id: int = 1) -> StaffContext:
    return StaffContext(
        business_id=business_id,
        staff_member_id=7,
        user_id=8,
        telegram_id=9,
        display_name="Мастер",
        role=StaffRole.MASTER,
        is_bookable=True,
    )


def build_uow() -> MagicMock:
    unit_of_work = MagicMock()
    unit_of_work.business_id = 1
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    member = SimpleNamespace(
        id=7,
        business_id=1,
        user_id=8,
        role=StaffRole.MASTER,
        is_active=True,
        archived_at=None,
        schedule_paused_until=None,
    )
    unit_of_work.staff.get_by_id = AsyncMock(return_value=member)
    unit_of_work.staff.flush = AsyncMock()
    unit_of_work.settings.get = AsyncMock(return_value=SimpleNamespace(timezone="Europe/Moscow"))
    unit_of_work.appointments.list_upcoming = AsyncMock(
        return_value=[
            (
                SimpleNamespace(
                    id=11,
                    client_id=12,
                    service_name_snapshot="Маникюр",
                    status=AppointmentStatus.CONFIRMED,
                ),
                SimpleNamespace(
                    start_at=datetime(2026, 8, 11, 9, tzinfo=UTC),
                    end_at=datetime(2026, 8, 11, 10, tzinfo=UTC),
                ),
            )
        ]
    )
    unit_of_work.users.get_by_id = AsyncMock(
        return_value=SimpleNamespace(first_name="Анна", phone="+79990000000")
    )

    async def weekly(
        business_id: int,
        staff_ids: list[int],
        weekday: int,
    ) -> list[object]:
        assert business_id == 1
        assert staff_ids == [7]
        if weekday != 0:
            return []
        return [
            SimpleNamespace(
                weekday=0,
                kind=ScheduleIntervalKind.WORK,
                start_minute=600,
                end_minute=1080,
            )
        ]

    unit_of_work.schedules.list_weekly_intervals = AsyncMock(side_effect=weekly)
    unit_of_work.schedules.list_date_exceptions = AsyncMock(return_value=[])
    unit_of_work.audit.add = AsyncMock()
    unit_of_work.commit = AsyncMock()
    return unit_of_work


def build_action_uow(
    *,
    start_at: datetime,
    end_at: datetime,
    staff_member_id: int = 7,
) -> tuple[MagicMock, SimpleNamespace, SimpleNamespace]:
    unit_of_work = build_uow()
    appointment = SimpleNamespace(
        id=11,
        business_id=1,
        staff_member_id=staff_member_id,
        client_id=12,
        window_id=13,
        service_name_snapshot="Маникюр",
        status=AppointmentStatus.CONFIRMED,
        completed_at=None,
        no_show_at=None,
        cancelled_at=None,
        cancellation_reason=None,
    )
    window = SimpleNamespace(
        id=13,
        business_id=1,
        staff_member_id=staff_member_id,
        start_at=start_at,
        end_at=end_at,
        status=AvailabilityWindowStatus.BOOKED,
    )
    client = SimpleNamespace(
        id=12,
        first_name="Анна",
        phone="+79990000000",
        marketing_consent_at=None,
        repeat_booking_opt_out_at=None,
        is_blocked=False,
    )
    unit_of_work.settings.get.return_value = SimpleNamespace(
        timezone="Europe/Moscow",
        reviews_enabled=False,
        review_request_delay_minutes=60,
        repeat_booking_reminder_days=30,
        waitlist_notification_cooldown_minutes=60,
    )
    unit_of_work.appointments.get = AsyncMock(return_value=appointment)
    unit_of_work.windows.get = AsyncMock(return_value=window)
    unit_of_work.windows.lock_local_date = AsyncMock()
    unit_of_work.windows.get_many_for_update = AsyncMock(return_value=[window])
    unit_of_work.users.get_by_id = AsyncMock(return_value=client)
    unit_of_work.appointments.add_history = AsyncMock()
    unit_of_work.reference_media.set_expiry_for_appointment = AsyncMock()
    unit_of_work.notifications.cancel_unsent = AsyncMock()
    unit_of_work.notifications.add_all = AsyncMock()
    unit_of_work.waitlist.list_matching = AsyncMock(return_value=[])
    return unit_of_work, appointment, window


@pytest.mark.asyncio
async def test_master_sees_only_repository_query_scoped_to_own_staff_id() -> None:
    unit_of_work = build_uow()
    service = MasterWorkspaceService(lambda: unit_of_work)  # type: ignore[arg-type]

    result = await service.list_upcoming_appointments(actor(), now=NOW)

    assert len(result) == 1
    assert result[0].client_name == "Анна"
    unit_of_work.appointments.list_upcoming.assert_awaited_once_with(
        NOW,
        limit=20,
        staff_member_id=7,
    )


@pytest.mark.asyncio
async def test_workspace_includes_recent_active_visits_but_remains_self_scoped() -> None:
    unit_of_work = build_uow()
    unit_of_work.appointments.list_between = AsyncMock(return_value=[])
    service = MasterWorkspaceService(lambda: unit_of_work)  # type: ignore[arg-type]

    await service.list_workspace_appointments(actor(), now=NOW)

    unit_of_work.appointments.list_between.assert_awaited_once_with(
        datetime(2026, 8, 9, 9, tzinfo=UTC),
        datetime(2026, 9, 9, 9, tzinfo=UTC),
        staff_member_id=7,
    )


@pytest.mark.asyncio
async def test_cross_business_context_is_rejected_before_any_data_query() -> None:
    unit_of_work = build_uow()
    service = MasterWorkspaceService(lambda: unit_of_work)  # type: ignore[arg-type]

    with pytest.raises(AuthorizationError):
        await service.list_upcoming_appointments(actor(business_id=2), now=NOW)

    unit_of_work.staff.get_by_id.assert_not_awaited()
    unit_of_work.appointments.list_upcoming.assert_not_awaited()


@pytest.mark.asyncio
async def test_archived_master_is_rejected_on_every_action() -> None:
    unit_of_work = build_uow()
    unit_of_work.staff.get_by_id.return_value.archived_at = NOW
    service = MasterWorkspaceService(lambda: unit_of_work)  # type: ignore[arg-type]

    with pytest.raises(AuthorizationError):
        await service.get_schedule(actor(), now=NOW)


@pytest.mark.asyncio
async def test_master_can_pause_only_own_online_schedule_with_audit() -> None:
    unit_of_work = build_uow()
    service = MasterWorkspaceService(lambda: unit_of_work)  # type: ignore[arg-type]

    result = await service.set_schedule_pause(
        actor(),
        pause_days=1,
        now=NOW,
        correlation_id="corr-master",
    )

    assert result.paused_until == datetime(2026, 8, 11, 9, tzinfo=UTC)
    unit_of_work.staff.flush.assert_awaited_once()
    unit_of_work.audit.add.assert_awaited_once()
    assert unit_of_work.audit.add.await_args.kwargs["correlation_id"] == "corr-master"
    unit_of_work.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_master_completes_only_own_ended_visit_with_history_and_audit() -> None:
    unit_of_work, appointment, window = build_action_uow(
        start_at=datetime(2026, 8, 10, 7, tzinfo=UTC),
        end_at=datetime(2026, 8, 10, 8, tzinfo=UTC),
    )
    service = MasterWorkspaceService(lambda: unit_of_work)  # type: ignore[arg-type]

    result = await service.complete_own_visit(
        actor(),
        11,
        now=NOW,
        correlation_id="corr-complete",
    )

    assert result.status is AppointmentStatus.COMPLETED
    assert appointment.completed_at == NOW
    assert window.status is AvailabilityWindowStatus.CLOSED
    unit_of_work.appointments.add_history.assert_awaited_once()
    unit_of_work.reference_media.set_expiry_for_appointment.assert_awaited_once()
    assert unit_of_work.audit.add.await_args.kwargs["actor_user_id"] == 8
    assert unit_of_work.audit.add.await_args.kwargs["correlation_id"] == "corr-complete"
    unit_of_work.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_master_cannot_mutate_another_masters_appointment() -> None:
    unit_of_work, appointment, _window = build_action_uow(
        start_at=datetime(2026, 8, 10, 7, tzinfo=UTC),
        end_at=datetime(2026, 8, 10, 8, tzinfo=UTC),
        staff_member_id=99,
    )
    service = MasterWorkspaceService(lambda: unit_of_work)  # type: ignore[arg-type]

    with pytest.raises(AuthorizationError):
        await service.complete_own_visit(actor(), appointment.id, now=NOW)

    unit_of_work.windows.lock_local_date.assert_not_awaited()
    unit_of_work.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_master_cannot_mark_no_show_before_visit_end() -> None:
    unit_of_work, appointment, _window = build_action_uow(
        start_at=datetime(2026, 8, 10, 10, tzinfo=UTC),
        end_at=datetime(2026, 8, 10, 11, tzinfo=UTC),
    )
    service = MasterWorkspaceService(lambda: unit_of_work)  # type: ignore[arg-type]

    with pytest.raises(AppointmentStateError, match="после окончания"):
        await service.mark_own_no_show(actor(), appointment.id, now=NOW)

    unit_of_work.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_master_can_cancel_only_own_future_visit_and_reopens_window() -> None:
    unit_of_work, appointment, window = build_action_uow(
        start_at=datetime(2026, 8, 11, 9, tzinfo=UTC),
        end_at=datetime(2026, 8, 11, 10, tzinfo=UTC),
    )
    service = MasterWorkspaceService(lambda: unit_of_work)  # type: ignore[arg-type]

    result = await service.cancel_own_appointment(actor(), appointment.id, now=NOW)

    assert result.status is AppointmentStatus.CANCELLED_BY_ADMIN
    assert appointment.cancellation_reason == "Отменено мастером"
    assert window.status is AvailabilityWindowStatus.OPEN
    unit_of_work.waitlist.list_matching.assert_awaited_once()
    unit_of_work.commit.assert_awaited_once()
