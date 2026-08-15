"""Appointment deadline and active-state rule tests."""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.appointments import (
    ensure_active_appointment,
    ensure_client_change_deadline,
    ensure_client_reschedule_deadline,
)
from app.domain.enums import AppointmentStatus
from app.domain.errors import AppointmentStateError, CancellationDeadlineError

NOW = datetime(2026, 7, 22, 9, tzinfo=UTC)


def test_exactly_36_hours_is_allowed() -> None:
    ensure_client_change_deadline(
        start_at=NOW + timedelta(hours=36),
        now=NOW,
        deadline_hours=36,
    )


def test_less_than_36_hours_is_blocked_with_required_message() -> None:
    with pytest.raises(CancellationDeadlineError, match="меньше 36 ч"):
        ensure_client_change_deadline(
            start_at=NOW + timedelta(hours=36) - timedelta(seconds=1),
            now=NOW,
            deadline_hours=36,
        )


def test_terminal_appointment_is_not_changeable() -> None:
    with pytest.raises(AppointmentStateError, match="не является активной"):
        ensure_active_appointment(AppointmentStatus.CANCELLED_BY_CLIENT)


def test_reschedule_deadline_is_independent_from_cancellation_deadline() -> None:
    start_at = NOW + timedelta(hours=30)

    ensure_client_reschedule_deadline(start_at=start_at, now=NOW, deadline_hours=24)
    with pytest.raises(CancellationDeadlineError, match="36 ч"):
        ensure_client_change_deadline(start_at=start_at, now=NOW, deadline_hours=36)


def test_reschedule_error_uses_configured_deadline() -> None:
    with pytest.raises(CancellationDeadlineError, match="24 ч"):
        ensure_client_reschedule_deadline(
            start_at=NOW + timedelta(hours=23),
            now=NOW,
            deadline_hours=24,
        )
