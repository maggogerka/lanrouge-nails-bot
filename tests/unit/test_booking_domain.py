"""Pure booking contact, fit and reminder scheduling tests."""

from datetime import UTC, datetime

import pytest

from app.domain.booking import (
    normalize_phone,
    validate_bookable_date,
    validate_service_fits_window,
)
from app.domain.enums import NotificationType
from app.domain.errors import BookingUnavailableError
from app.domain.notifications import future_reminder_schedules


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("8 (999) 123-45-67", "+79991234567"),
        ("9991234567", "+79991234567"),
        ("+44 20 7946 0958", "+442079460958"),
    ],
)
def test_phone_normalization(raw: str, expected: str) -> None:
    assert normalize_phone(raw) == expected


@pytest.mark.parametrize("raw", ["123", "+7abc", "", "+1234567890123456"])
def test_invalid_phone_is_rejected(raw: str) -> None:
    with pytest.raises(ValueError, match="корректный номер"):
        normalize_phone(raw)


def test_service_fit_uses_maximum_duration() -> None:
    with pytest.raises(BookingUnavailableError, match="не помещается"):
        validate_service_fits_window(
            duration_max_minutes=211,
            start_at=datetime(2026, 7, 23, 7, tzinfo=UTC),
            end_at=datetime(2026, 7, 23, 10, 30, tzinfo=UTC),
        )


def test_current_weekend_rule_is_rechecked_for_persisted_window() -> None:
    with pytest.raises(BookingUnavailableError, match="субботу"):
        validate_bookable_date(
            start_at=datetime(2026, 7, 25, 7, tzinfo=UTC),
            now=datetime(2026, 7, 22, 9, tzinfo=UTC),
            timezone="Europe/Moscow",
            booking_horizon_days=31,
            allow_saturday=False,
            allow_sunday=False,
        )


def test_only_strictly_future_reminders_are_built_for_each_recipient() -> None:
    schedules = future_reminder_schedules(
        start_at=datetime(2026, 7, 23, 11, tzinfo=UTC),
        now=datetime(2026, 7, 23, 9, tzinfo=UTC),
        offsets_minutes=[1440, 180, 60],
        client_user_id=5,
        admin_user_ids=[8],
    )

    assert len(schedules) == 2
    assert {schedule.offset_minutes for schedule in schedules} == {60}
    assert {schedule.notification_type for schedule in schedules} == {
        NotificationType.CLIENT_REMINDER,
        NotificationType.ADMIN_REMINDER,
    }


def test_reminder_at_exactly_now_is_not_future() -> None:
    schedules = future_reminder_schedules(
        start_at=datetime(2026, 7, 23, 10, tzinfo=UTC),
        now=datetime(2026, 7, 23, 9, tzinfo=UTC),
        offsets_minutes=[60],
        client_user_id=5,
        admin_user_ids=[],
    )

    assert schedules == []
