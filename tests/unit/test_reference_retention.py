"""Status-specific reference retention calculations."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.domain.enums import AppointmentStatus
from app.domain.reference_retention import ReferenceRetentionPolicy

NOW = datetime(2026, 7, 24, 9, tzinfo=UTC)
POLICY = ReferenceRetentionPolicy(completed_days=30, cancelled_days=7, no_show_days=14)


@pytest.mark.parametrize(
    ("status", "timestamp_field", "days"),
    [
        (AppointmentStatus.COMPLETED, "completed_at", 30),
        (AppointmentStatus.CANCELLED_BY_CLIENT, "cancelled_at", 7),
        (AppointmentStatus.CANCELLED_BY_ADMIN, "cancelled_at", 7),
    ],
)
def test_terminal_status_uses_its_actual_timestamp(
    status: AppointmentStatus, timestamp_field: str, days: int
) -> None:
    values = {timestamp_field: NOW}

    expiry = POLICY.expires_at(
        status=status,
        planned_end_at=NOW - timedelta(days=10),
        **values,  # type: ignore[arg-type]
    )

    assert expiry == NOW + timedelta(days=days)
    assert expiry.tzinfo is UTC


def test_no_show_uses_planned_end_instead_of_status_timestamp() -> None:
    planned_end = NOW - timedelta(days=10)

    expiry = POLICY.expires_at(
        status=AppointmentStatus.NO_SHOW,
        planned_end_at=planned_end,
    )

    assert expiry == planned_end + timedelta(days=14)


@pytest.mark.parametrize(
    "status",
    [
        AppointmentStatus.CONFIRMED,
        AppointmentStatus.CLIENT_CONFIRMED,
        AppointmentStatus.RESCHEDULED,
    ],
)
def test_non_terminal_or_stale_active_status_uses_planned_end(status: AppointmentStatus) -> None:
    planned_end = NOW + timedelta(days=3)

    assert POLICY.expires_at(status=status, planned_end_at=planned_end) == planned_end + timedelta(
        days=30
    )


def test_future_active_reference_cannot_expire_before_visit() -> None:
    planned_end = NOW + timedelta(days=100)

    expiry = POLICY.expires_at(
        status=AppointmentStatus.CONFIRMED,
        planned_end_at=planned_end,
    )

    assert expiry > planned_end > NOW


@pytest.mark.parametrize(
    ("status", "age", "is_expired"),
    [
        (AppointmentStatus.COMPLETED, timedelta(days=29), False),
        (AppointmentStatus.COMPLETED, timedelta(days=31), True),
        (AppointmentStatus.CANCELLED_BY_CLIENT, timedelta(days=6), False),
        (AppointmentStatus.CANCELLED_BY_ADMIN, timedelta(days=8), True),
        (AppointmentStatus.NO_SHOW, timedelta(days=15), True),
    ],
)
def test_terminal_retention_boundary(
    status: AppointmentStatus,
    age: timedelta,
    is_expired: bool,
) -> None:
    event_at = NOW - age
    expiry = POLICY.expires_at(
        status=status,
        planned_end_at=event_at,
        completed_at=event_at,
        cancelled_at=event_at,
    )

    assert (expiry <= NOW) is is_expired


def test_moscow_timestamp_is_normalized_to_utc() -> None:
    moscow_end = datetime(2026, 7, 24, 12, tzinfo=ZoneInfo("Europe/Moscow"))

    expiry = POLICY.expires_at(
        status=AppointmentStatus.CONFIRMED,
        planned_end_at=moscow_end,
    )

    assert expiry == datetime(2026, 8, 23, 9, tzinfo=UTC)
    assert expiry.tzinfo is UTC


def test_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        POLICY.expires_at(
            status=AppointmentStatus.CONFIRMED,
            planned_end_at=datetime(2026, 7, 24, 12),
        )
