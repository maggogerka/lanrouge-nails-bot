"""Pure, lazy projection of per-staff schedules into one bookable local day."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from itertools import pairwise
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.domain.enums import BusinessType, ScheduleExceptionKind, ScheduleIntervalKind
from app.domain.errors import WindowValidationError

_ALL_DAY_EXCEPTION_KINDS = frozenset(
    {
        ScheduleExceptionKind.DAY_OFF,
        ScheduleExceptionKind.LEAVE,
        ScheduleExceptionKind.SICK,
    }
)
_TIMED_EXCEPTION_KINDS = frozenset(
    {ScheduleExceptionKind.WORKING_WINDOW, ScheduleExceptionKind.BREAK}
)


@dataclass(frozen=True, order=True, slots=True)
class LocalMinuteRange:
    """A half-open local wall-clock range; 1440 represents next midnight."""

    start: int
    end: int

    def __post_init__(self) -> None:
        if not 0 <= self.start < self.end <= 1440:
            raise WindowValidationError("Schedule interval must be within one local day")

    @classmethod
    def from_times(cls, start: time, end: time) -> LocalMinuteRange:
        if start.second or start.microsecond or end.second or end.microsecond:
            raise WindowValidationError("Schedule precision is one minute")
        start_minute = start.hour * 60 + start.minute
        end_minute = end.hour * 60 + end.minute
        if end_minute == 0 and start_minute > 0:
            end_minute = 1440
        return cls(start_minute, end_minute)

    def overlaps(self, other: LocalMinuteRange) -> bool:
        return self.start < other.end and other.start < self.end


@dataclass(frozen=True, slots=True)
class WeeklyScheduleRule:
    weekday: int
    kind: ScheduleIntervalKind
    interval: LocalMinuteRange

    def __post_init__(self) -> None:
        if not 0 <= self.weekday <= 6:
            raise WindowValidationError("Weekday must be between 0 and 6")


@dataclass(frozen=True, slots=True)
class DateScheduleException:
    local_date: date
    kind: ScheduleExceptionKind
    interval: LocalMinuteRange | None = None

    def __post_init__(self) -> None:
        if self.kind in _TIMED_EXCEPTION_KINDS and self.interval is None:
            raise WindowValidationError("Timed schedule exception requires an interval")
        if self.kind in _ALL_DAY_EXCEPTION_KINDS and self.interval is not None:
            raise WindowValidationError("All-day schedule exception cannot have an interval")


@dataclass(frozen=True, slots=True)
class BusyPeriod:
    """An already occupied half-open UTC interval."""

    start_at: datetime
    end_at: datetime

    def __post_init__(self) -> None:
        if self.start_at.tzinfo is None or self.end_at.tzinfo is None:
            raise WindowValidationError("Busy periods must be timezone-aware")
        if self.start_at >= self.end_at:
            raise WindowValidationError("Busy period must have positive duration")


@dataclass(frozen=True, slots=True)
class StaffScheduleDefinition:
    staff_member_id: int
    display_name: str
    duration_minutes: int
    weekly_rules: tuple[WeeklyScheduleRule, ...]
    date_exceptions: tuple[DateScheduleException, ...] = ()
    busy_periods: tuple[BusyPeriod, ...] = ()
    sort_order: int = 0
    is_active: bool = True
    is_bookable: bool = True
    schedule_paused_until: datetime | None = None

    def __post_init__(self) -> None:
        if self.staff_member_id <= 0:
            raise WindowValidationError("Staff ID must be positive")
        if not self.display_name.strip():
            raise WindowValidationError("Staff display name must not be empty")
        if not 1 <= self.duration_minutes <= 1440:
            raise WindowValidationError("Service duration must be between 1 and 1440 minutes")
        if self.schedule_paused_until is not None and self.schedule_paused_until.tzinfo is None:
            raise WindowValidationError("Schedule pause timestamp must be timezone-aware")
        _ensure_weekly_rules_do_not_overlap(self.weekly_rules)
        _ensure_date_exceptions_do_not_overlap(self.date_exceptions)


@dataclass(frozen=True, slots=True)
class ProjectedSlot:
    staff_member_id: int
    staff_display_name: str
    start_at: datetime
    end_at: datetime
    local_start: datetime
    local_end: datetime


@dataclass(frozen=True, slots=True)
class DayAvailabilityProjection:
    local_date: date
    timezone: str
    eligible_staff_ids: tuple[int, ...]
    show_master_selection: bool
    slots: tuple[ProjectedSlot, ...]


class AvailabilityProjector:
    """Generate slots for one requested day only; no slots are persisted or pre-generated."""

    def project_day(
        self,
        *,
        business_type: BusinessType,
        timezone: str,
        local_date: date,
        now: datetime,
        booking_horizon_days: int,
        staff_schedules: tuple[StaffScheduleDefinition, ...],
        requested_staff_id: int | None = None,
        slot_step_minutes: int = 30,
    ) -> DayAvailabilityProjection:
        if now.tzinfo is None:
            raise WindowValidationError("Current time must be timezone-aware")
        if not 1 <= booking_horizon_days <= 365:
            raise WindowValidationError("Booking horizon must be between 1 and 365 days")
        if not 1 <= slot_step_minutes <= 1440:
            raise WindowValidationError("Slot step must be between 1 and 1440 minutes")
        try:
            zone = ZoneInfo(timezone)
        except ZoneInfoNotFoundError as exc:
            raise WindowValidationError("Unknown business timezone") from exc

        current_time = now.astimezone(UTC)
        today = current_time.astimezone(zone).date()
        if not today <= local_date <= today + timedelta(days=booking_horizon_days):
            raise WindowValidationError("Requested date is outside the booking horizon")

        eligible = sorted(
            (staff for staff in staff_schedules if staff.is_active and staff.is_bookable),
            key=lambda staff: (
                staff.sort_order,
                staff.display_name.casefold(),
                staff.staff_member_id,
            ),
        )
        eligible_ids = tuple(staff.staff_member_id for staff in eligible)
        if len(eligible_ids) != len(set(eligible_ids)):
            raise WindowValidationError("Staff schedule definitions must be unique")
        if business_type is BusinessType.SOLO and len(eligible) > 1:
            raise WindowValidationError("Solo business cannot expose multiple bookable masters")

        projected_staff = eligible
        if requested_staff_id is not None:
            projected_staff = [
                staff for staff in eligible if staff.staff_member_id == requested_staff_id
            ]
            if not projected_staff:
                raise WindowValidationError("Requested master is not bookable")

        slots = [
            slot
            for staff in projected_staff
            for slot in self._project_staff_day(
                staff,
                local_date=local_date,
                zone=zone,
                now=current_time,
                slot_step_minutes=slot_step_minutes,
            )
        ]
        staff_order = {staff.staff_member_id: staff.sort_order for staff in projected_staff}
        slots.sort(
            key=lambda slot: (
                slot.start_at,
                staff_order[slot.staff_member_id],
                slot.staff_member_id,
                slot.end_at,
            )
        )
        return DayAvailabilityProjection(
            local_date=local_date,
            timezone=timezone,
            eligible_staff_ids=eligible_ids,
            show_master_selection=(business_type is BusinessType.SALON and len(eligible_ids) > 1),
            slots=tuple(slots),
        )

    def _project_staff_day(
        self,
        staff: StaffScheduleDefinition,
        *,
        local_date: date,
        zone: ZoneInfo,
        now: datetime,
        slot_step_minutes: int,
    ) -> list[ProjectedSlot]:
        work_ranges = _effective_work_ranges(staff, local_date)
        slots: list[ProjectedSlot] = []
        duration = timedelta(minutes=staff.duration_minutes)
        step = timedelta(minutes=slot_step_minutes)
        pause_until = (
            staff.schedule_paused_until.astimezone(UTC)
            if staff.schedule_paused_until is not None
            else None
        )

        for work_range in work_ranges:
            range_start = _resolve_boundary(local_date, work_range.start, zone, prefer_late=False)
            range_end = _resolve_boundary(local_date, work_range.end, zone, prefer_late=True)
            cursor = range_start
            while cursor + duration <= range_end:
                slot_end = cursor + duration
                if (
                    cursor > now
                    and (pause_until is None or cursor >= pause_until)
                    and not any(
                        cursor < busy.end_at.astimezone(UTC)
                        and busy.start_at.astimezone(UTC) < slot_end
                        for busy in staff.busy_periods
                    )
                ):
                    slots.append(
                        ProjectedSlot(
                            staff_member_id=staff.staff_member_id,
                            staff_display_name=staff.display_name,
                            start_at=cursor,
                            end_at=slot_end,
                            local_start=cursor.astimezone(zone),
                            local_end=slot_end.astimezone(zone),
                        )
                    )
                cursor += step
        return slots


def _effective_work_ranges(
    staff: StaffScheduleDefinition, local_date: date
) -> tuple[LocalMinuteRange, ...]:
    exceptions = tuple(
        exception for exception in staff.date_exceptions if exception.local_date == local_date
    )
    if any(exception.kind in _ALL_DAY_EXCEPTION_KINDS for exception in exceptions):
        return ()

    custom_work = tuple(
        exception.interval
        for exception in exceptions
        if exception.kind is ScheduleExceptionKind.WORKING_WINDOW and exception.interval is not None
    )
    weekly_work = tuple(
        rule.interval
        for rule in staff.weekly_rules
        if rule.weekday == local_date.weekday() and rule.kind is ScheduleIntervalKind.WORK
    )
    work_ranges = custom_work or weekly_work
    breaks = tuple(
        rule.interval
        for rule in staff.weekly_rules
        if rule.weekday == local_date.weekday() and rule.kind is ScheduleIntervalKind.BREAK
    ) + tuple(
        exception.interval
        for exception in exceptions
        if exception.kind is ScheduleExceptionKind.BREAK and exception.interval is not None
    )
    return _subtract_ranges(work_ranges, breaks)


def _subtract_ranges(
    sources: tuple[LocalMinuteRange, ...],
    exclusions: tuple[LocalMinuteRange, ...],
) -> tuple[LocalMinuteRange, ...]:
    result = list(sorted(sources))
    for exclusion in sorted(exclusions):
        remaining: list[LocalMinuteRange] = []
        for source in result:
            if not source.overlaps(exclusion):
                remaining.append(source)
                continue
            if source.start < exclusion.start:
                remaining.append(LocalMinuteRange(source.start, min(source.end, exclusion.start)))
            if exclusion.end < source.end:
                remaining.append(LocalMinuteRange(max(source.start, exclusion.end), source.end))
        result = remaining
    return tuple(result)


def _ensure_weekly_rules_do_not_overlap(rules: tuple[WeeklyScheduleRule, ...]) -> None:
    grouped: dict[tuple[int, ScheduleIntervalKind], list[LocalMinuteRange]] = {}
    for rule in rules:
        grouped.setdefault((rule.weekday, rule.kind), []).append(rule.interval)
    for intervals in grouped.values():
        _ensure_ranges_do_not_overlap(intervals)


def _ensure_date_exceptions_do_not_overlap(
    exceptions: tuple[DateScheduleException, ...],
) -> None:
    grouped: dict[tuple[date, ScheduleExceptionKind], list[LocalMinuteRange]] = {}
    all_day_dates: set[date] = set()
    for exception in exceptions:
        if exception.kind in _ALL_DAY_EXCEPTION_KINDS:
            if exception.local_date in all_day_dates:
                raise WindowValidationError("Only one all-day exception is allowed per date")
            all_day_dates.add(exception.local_date)
        elif exception.interval is not None:
            grouped.setdefault((exception.local_date, exception.kind), []).append(
                exception.interval
            )
    for intervals in grouped.values():
        _ensure_ranges_do_not_overlap(intervals)


def _ensure_ranges_do_not_overlap(intervals: list[LocalMinuteRange]) -> None:
    ordered = sorted(intervals)
    for previous, current in pairwise(ordered):
        if previous.overlaps(current):
            raise WindowValidationError("Schedule intervals of the same kind cannot overlap")


def _resolve_boundary(
    local_date: date,
    minute: int,
    zone: ZoneInfo,
    *,
    prefer_late: bool,
) -> datetime:
    boundary_date = local_date + timedelta(days=minute // 1440)
    minute_in_day = minute % 1440
    naive = datetime.combine(
        boundary_date,
        time(hour=minute_in_day // 60, minute=minute_in_day % 60),
    )
    for shift in range(181):
        candidate = naive + timedelta(minutes=shift)
        instants = _valid_utc_instants(candidate, zone)
        if instants:
            return max(instants) if prefer_late else min(instants)
    raise WindowValidationError("Schedule boundary falls inside an unresolved timezone gap")


def _valid_utc_instants(local_naive: datetime, zone: ZoneInfo) -> tuple[datetime, ...]:
    instants: set[datetime] = set()
    for fold in (0, 1):
        local = local_naive.replace(tzinfo=zone, fold=fold)
        utc = local.astimezone(UTC)
        round_trip = utc.astimezone(zone)
        if round_trip.replace(tzinfo=None) == local_naive and round_trip.fold == fold:
            instants.add(utc)
    return tuple(sorted(instants))
