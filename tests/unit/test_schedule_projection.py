"""Lazy multi-master schedule projection, overlap, and DST tests."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from app.domain.enums import BusinessType, ScheduleExceptionKind, ScheduleIntervalKind
from app.domain.errors import WindowValidationError
from app.domain.schedule_projection import (
    AvailabilityProjector,
    BusyPeriod,
    DateScheduleException,
    LocalMinuteRange,
    StaffScheduleDefinition,
    WeeklyScheduleRule,
)


def rule(
    start: int,
    end: int,
    *,
    weekday: int = 0,
    kind: ScheduleIntervalKind = ScheduleIntervalKind.WORK,
) -> WeeklyScheduleRule:
    return WeeklyScheduleRule(weekday, kind, LocalMinuteRange(start, end))


def staff(
    staff_id: int,
    *rules: WeeklyScheduleRule,
    duration: int = 60,
    exceptions: tuple[DateScheduleException, ...] = (),
    busy: tuple[BusyPeriod, ...] = (),
) -> StaffScheduleDefinition:
    return StaffScheduleDefinition(
        staff_member_id=staff_id,
        display_name=f"Master {staff_id}",
        duration_minutes=duration,
        weekly_rules=tuple(rules),
        date_exceptions=exceptions,
        busy_periods=busy,
        sort_order=staff_id,
    )


def project(
    schedules: tuple[StaffScheduleDefinition, ...],
    *,
    business_type: BusinessType = BusinessType.SOLO,
    local_date: date = date(2026, 8, 10),
    timezone: str = "Europe/Moscow",
    now: datetime = datetime(2026, 8, 9, 9, tzinfo=UTC),
    horizon: int = 60,
    step: int = 30,
):
    return AvailabilityProjector().project_day(
        business_type=business_type,
        timezone=timezone,
        local_date=local_date,
        now=now,
        booking_horizon_days=horizon,
        staff_schedules=schedules,
        slot_step_minutes=step,
    )


def test_multiple_work_intervals_and_nested_break_are_projected_lazily() -> None:
    schedule = staff(
        1,
        rule(9 * 60, 13 * 60),
        rule(14 * 60, 18 * 60),
        rule(11 * 60, 11 * 60 + 30, kind=ScheduleIntervalKind.BREAK),
    )

    result = project((schedule,))
    starts = [(slot.local_start.hour, slot.local_start.minute) for slot in result.slots]

    assert (10, 30) not in starts
    assert (11, 0) not in starts
    assert (11, 30) in starts
    assert (14, 0) in starts
    assert all(slot.local_start.date() == date(2026, 8, 10) for slot in result.slots)


def test_same_kind_overlap_is_rejected_but_break_can_overlap_work() -> None:
    with pytest.raises(WindowValidationError, match="cannot overlap"):
        staff(1, rule(9 * 60, 12 * 60), rule(11 * 60, 13 * 60))

    valid = staff(
        1,
        rule(9 * 60, 12 * 60),
        rule(10 * 60, 11 * 60, kind=ScheduleIntervalKind.BREAK),
    )
    assert len(valid.weekly_rules) == 2


def test_all_day_exception_closes_staff_schedule() -> None:
    closed = DateScheduleException(date(2026, 8, 10), ScheduleExceptionKind.SICK)
    schedule = staff(1, rule(9 * 60, 18 * 60), exceptions=(closed,))

    assert project((schedule,)).slots == ()


def test_custom_working_window_replaces_weekly_work() -> None:
    custom = DateScheduleException(
        date(2026, 8, 10),
        ScheduleExceptionKind.WORKING_WINDOW,
        LocalMinuteRange(18 * 60, 20 * 60),
    )
    schedule = staff(1, rule(9 * 60, 12 * 60), exceptions=(custom,))

    starts = [slot.local_start.hour for slot in project((schedule,), step=60).slots]

    assert starts == [18, 19]


def test_busy_period_filters_every_partially_overlapping_slot() -> None:
    busy = BusyPeriod(
        datetime(2026, 8, 10, 6, 30, tzinfo=UTC),
        datetime(2026, 8, 10, 7, 30, tzinfo=UTC),
    )
    schedule = staff(1, rule(9 * 60, 12 * 60), busy=(busy,))

    starts = [
        (slot.local_start.hour, slot.local_start.minute) for slot in project((schedule,)).slots
    ]

    assert starts == [(10, 30), (11, 0)]


def test_solo_hides_master_selection_and_salon_exposes_independent_masters() -> None:
    first = staff(1, rule(9 * 60, 11 * 60))
    solo = project((first,), step=60)

    assert not solo.show_master_selection
    assert solo.eligible_staff_ids == (1,)

    second = staff(2, rule(9 * 60, 11 * 60))
    salon = project((first, second), business_type=BusinessType.SALON, step=60)

    assert salon.show_master_selection
    assert salon.eligible_staff_ids == (1, 2)
    assert [(slot.local_start.hour, slot.staff_member_id) for slot in salon.slots] == [
        (9, 1),
        (9, 2),
        (10, 1),
        (10, 2),
    ]


@pytest.mark.parametrize("horizon", [0, 366])
def test_horizon_is_restricted_to_one_through_365(horizon: int) -> None:
    with pytest.raises(WindowValidationError, match="between 1 and 365"):
        project((staff(1, rule(9 * 60, 11 * 60)),), horizon=horizon)


def test_date_beyond_horizon_is_rejected() -> None:
    with pytest.raises(WindowValidationError, match="outside"):
        project(
            (staff(1, rule(9 * 60, 11 * 60)),),
            local_date=date(2027, 8, 11),
            horizon=365,
        )


def test_365_day_horizon_boundary_is_available() -> None:
    result = project(
        (staff(1, rule(9 * 60, 11 * 60, weekday=0)),),
        local_date=date(2027, 8, 9),
        horizon=365,
    )

    assert result.local_date == date(2027, 8, 9)


def test_spring_forward_never_projects_nonexistent_wall_times() -> None:
    spring_sunday = staff(1, rule(60, 4 * 60, weekday=6), duration=30)

    result = project(
        (spring_sunday,),
        timezone="America/New_York",
        local_date=date(2026, 3, 8),
        now=datetime(2026, 3, 1, tzinfo=UTC),
    )

    assert result.slots
    assert all(slot.local_start.hour != 2 for slot in result.slots)
    assert all(slot.end_at > slot.start_at for slot in result.slots)


def test_fall_back_projects_both_real_folds_as_distinct_utc_slots() -> None:
    fall_sunday = staff(1, rule(30, 150, weekday=6), duration=30)

    result = project(
        (fall_sunday,),
        timezone="America/New_York",
        local_date=date(2026, 11, 1),
        now=datetime(2026, 10, 25, tzinfo=UTC),
    )
    repeated = [
        slot
        for slot in result.slots
        if slot.local_start.hour == 1 and slot.local_start.minute == 30
    ]

    assert len(repeated) == 2
    assert {slot.local_start.fold for slot in repeated} == {0, 1}
    assert repeated[0].start_at != repeated[1].start_at
