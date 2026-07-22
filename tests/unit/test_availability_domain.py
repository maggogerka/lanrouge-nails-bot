"""Framework-independent availability rule tests."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import pytest

from app.domain.availability import (
    ExistingInterval,
    WindowRules,
    local_window_to_utc,
    validate_calendar_rules,
    validate_capacity_and_spacing,
)
from app.domain.errors import WindowValidationError

NOW = datetime(2026, 7, 22, 9, tzinfo=UTC)


def rules(**overrides: object) -> WindowRules:
    values: dict[str, object] = {
        "timezone": "Europe/Moscow",
        "booking_horizon_days": 31,
        "max_windows_per_day": 2,
        "default_duration_minutes": 210,
        "minimum_gap_minutes": 60,
        "allow_saturday": False,
        "allow_sunday": False,
    }
    values.update(overrides)
    return WindowRules(**values)  # type: ignore[arg-type]


def test_local_window_is_converted_to_utc() -> None:
    start_at, end_at = local_window_to_utc(
        date(2026, 7, 23),
        time(10),
        210,
        "Europe/Moscow",
    )

    assert start_at == datetime(2026, 7, 23, 7, tzinfo=UTC)
    assert end_at == datetime(2026, 7, 23, 10, 30, tzinfo=UTC)


@pytest.mark.parametrize(
    ("local_date", "local_time", "duration", "message"),
    [
        (date(2026, 7, 22), time(11), 60, "будущем"),
        (date(2026, 8, 23), time(10), 60, "31 дней"),
        (date(2026, 7, 25), time(10), 60, "субботу"),
        (date(2026, 7, 26), time(10), 60, "воскресенье"),
        (date(2026, 7, 23), time(23), 120, "один день"),
    ],
)
def test_calendar_rules_reject_invalid_windows(
    local_date: date,
    local_time: time,
    duration: int,
    message: str,
) -> None:
    start_at, end_at = local_window_to_utc(
        local_date,
        local_time,
        duration,
        "Europe/Moscow",
    )

    with pytest.raises(WindowValidationError, match=message):
        validate_calendar_rules(
            local_date=local_date,
            start_at=start_at,
            end_at=end_at,
            now=NOW,
            rules=rules(),
        )


def test_booking_horizon_is_inclusive() -> None:
    local_date = date(2026, 8, 22)
    start_at, end_at = local_window_to_utc(local_date, time(10), 60, "Europe/Moscow")

    validate_calendar_rules(
        local_date=local_date,
        start_at=start_at,
        end_at=end_at,
        now=NOW,
        rules=rules(allow_saturday=True),
    )


def test_daily_active_window_limit_is_enforced() -> None:
    existing = [
        ExistingInterval(
            datetime(2026, 7, 23, hour, tzinfo=UTC),
            datetime(2026, 7, 23, hour + 1, tzinfo=UTC),
        )
        for hour in (7, 11)
    ]

    with pytest.raises(WindowValidationError, match="максимальное число"):
        validate_capacity_and_spacing(
            start_at=datetime(2026, 7, 23, 15, tzinfo=UTC),
            end_at=datetime(2026, 7, 23, 16, tzinfo=UTC),
            existing=existing,
            max_windows_per_day=2,
            minimum_gap_minutes=60,
        )


def test_overlap_is_rejected() -> None:
    with pytest.raises(WindowValidationError, match="пересекается"):
        validate_capacity_and_spacing(
            start_at=datetime(2026, 7, 23, 8, tzinfo=UTC),
            end_at=datetime(2026, 7, 23, 10, tzinfo=UTC),
            existing=[
                ExistingInterval(
                    datetime(2026, 7, 23, 7, tzinfo=UTC),
                    datetime(2026, 7, 23, 9, tzinfo=UTC),
                )
            ],
            max_windows_per_day=2,
            minimum_gap_minutes=60,
        )


@pytest.mark.parametrize(("gap_minutes", "valid"), [(59, False), (60, True)])
def test_minimum_gap_boundary(gap_minutes: int, valid: bool) -> None:
    existing = [
        ExistingInterval(
            datetime(2026, 7, 23, 7, tzinfo=UTC),
            datetime(2026, 7, 23, 9, tzinfo=UTC),
        )
    ]
    start_at = datetime(2026, 7, 23, 9, tzinfo=UTC) + timedelta(minutes=gap_minutes)
    kwargs = {
        "start_at": start_at,
        "end_at": start_at + timedelta(hours=1),
        "existing": existing,
        "max_windows_per_day": 2,
        "minimum_gap_minutes": 60,
    }

    if valid:
        validate_capacity_and_spacing(**kwargs)  # type: ignore[arg-type]
    else:
        with pytest.raises(WindowValidationError, match="не менее 60 минут"):
            validate_capacity_and_spacing(**kwargs)  # type: ignore[arg-type]
