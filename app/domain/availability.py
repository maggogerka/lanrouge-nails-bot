"""Framework-independent calendar and spacing rules."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.domain.errors import WindowValidationError


@dataclass(frozen=True, slots=True)
class WindowRules:
    """Current settings required to validate a window."""

    timezone: str
    booking_horizon_days: int
    max_windows_per_day: int
    default_duration_minutes: int
    minimum_gap_minutes: int
    allow_saturday: bool
    allow_sunday: bool


@dataclass(frozen=True, slots=True)
class ExistingInterval:
    """An active UTC interval participating in overlap/gap checks."""

    start_at: datetime
    end_at: datetime


def local_window_to_utc(
    local_date: date,
    local_start_time: time,
    duration_minutes: int,
    timezone: str,
) -> tuple[datetime, datetime]:
    """Convert one local wall-clock interval to aware UTC timestamps."""

    zone = ZoneInfo(timezone)
    local_start = datetime.combine(local_date, local_start_time, tzinfo=zone)
    start_at = local_start.astimezone(UTC)
    end_at = (local_start + timedelta(minutes=duration_minutes)).astimezone(UTC)
    return start_at, end_at


def utc_day_bounds(local_date: date, timezone: str) -> tuple[datetime, datetime]:
    """Return the UTC instants surrounding one local business date."""

    zone = ZoneInfo(timezone)
    start = datetime.combine(local_date, time.min, tzinfo=zone).astimezone(UTC)
    end = datetime.combine(local_date + timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC)
    return start, end


def validate_calendar_rules(
    *,
    local_date: date,
    start_at: datetime,
    end_at: datetime,
    now: datetime,
    rules: WindowRules,
) -> None:
    """Validate future time, horizon, weekend and duration."""

    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if start_at.tzinfo is None or end_at.tzinfo is None:
        raise ValueError("window timestamps must be timezone-aware")
    if start_at >= end_at:
        raise WindowValidationError("Продолжительность окна должна быть положительной.")
    if start_at <= now:
        raise WindowValidationError("Окно должно начинаться в будущем.")

    today = now.astimezone(ZoneInfo(rules.timezone)).date()
    if end_at.astimezone(ZoneInfo(rules.timezone)).date() != local_date:
        raise WindowValidationError("Окно должно начинаться и заканчиваться в один день.")
    if local_date < today or local_date > today + timedelta(days=rules.booking_horizon_days):
        raise WindowValidationError(
            f"Дата должна находиться в пределах {rules.booking_horizon_days} дней."
        )
    if local_date.weekday() == 5 and not rules.allow_saturday:
        raise WindowValidationError("Создание окон в субботу отключено.")
    if local_date.weekday() == 6 and not rules.allow_sunday:
        raise WindowValidationError("Создание окон в воскресенье отключено.")


def validate_capacity_and_spacing(
    *,
    start_at: datetime,
    end_at: datetime,
    existing: list[ExistingInterval],
    max_windows_per_day: int,
    minimum_gap_minutes: int,
) -> None:
    """Enforce the conservative active-window cap, overlap and minimum gap."""

    if len(existing) >= max_windows_per_day:
        raise WindowValidationError(
            f"На эту дату уже создано максимальное число активных окон: {max_windows_per_day}."
        )

    minimum_gap = timedelta(minutes=minimum_gap_minutes)
    for interval in existing:
        if start_at < interval.end_at and interval.start_at < end_at:
            raise WindowValidationError("Новое окно пересекается с существующим.")
        if end_at <= interval.start_at and interval.start_at - end_at < minimum_gap:
            raise WindowValidationError(
                f"Между окнами должно быть не менее {minimum_gap_minutes} минут."
            )
        if interval.end_at <= start_at and start_at - interval.end_at < minimum_gap:
            raise WindowValidationError(
                f"Между окнами должно быть не менее {minimum_gap_minutes} минут."
            )
