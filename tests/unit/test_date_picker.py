"""Sequential date-page and Telegram calendar tests."""

from datetime import date, timedelta

import pytest

from app.domain.errors import DatePickerValidationError
from app.keyboards.common.date_picker import DatePickerCallback, date_picker_keyboard
from app.services.date_picker_service import DatePickerService

service = DatePickerService()


def build(start: date, **overrides: object):  # type: ignore[no-untyped-def]
    values: dict[str, object] = {
        "today": start,
        "requested_start": start,
        "booking_horizon_days": 90,
        "allow_saturday": True,
        "allow_sunday": True,
    }
    values.update(overrides)
    return service.build_page(**values)  # type: ignore[arg-type]


def test_page_contains_exactly_31_sequential_dates_across_months() -> None:
    page = build(date(2026, 7, 25))

    assert len(page.days) == 31
    assert page.start_date == date(2026, 7, 25)
    assert page.end_date == date(2026, 8, 24)
    assert [item.local_date for item in page.days] == [
        page.start_date + timedelta(days=offset) for offset in range(31)
    ]


@pytest.mark.parametrize(
    ("start", "expected"),
    [
        (date(2026, 4, 25), date(2026, 5, 25)),
        (date(2026, 2, 1), date(2026, 3, 3)),
        (date(2024, 2, 1), date(2024, 3, 2)),
    ],
)
def test_calendar_arithmetic_handles_short_months_and_leap_year(
    start: date,
    expected: date,
) -> None:
    page = build(start)

    assert page.end_date == expected
    assert len({item.local_date for item in page.days}) == 31


def test_page_is_truncated_at_booking_horizon() -> None:
    page = build(date(2026, 7, 23), booking_horizon_days=10)

    assert len(page.days) == 11
    assert page.end_date == date(2026, 8, 2)
    assert page.next_start is None


def test_navigation_never_points_before_today_or_after_horizon() -> None:
    first = build(date(2026, 7, 23), booking_horizon_days=40)
    second = service.build_page(
        today=first.today,
        requested_start=first.next_start,
        booking_horizon_days=40,
        allow_saturday=True,
        allow_sunday=True,
    )

    assert first.previous_start is None
    assert second.previous_start == first.today
    assert second.end_date == date(2026, 9, 1)
    assert second.next_start is None


def test_weekends_remain_visible_but_use_noop_callback() -> None:
    page = build(
        date(2026, 7, 25),
        allow_saturday=False,
        allow_sunday=False,
    )
    keyboard = date_picker_keyboard(page)
    buttons = [button for row in keyboard.inline_keyboard for button in row]
    saturday = next(button for button in buttons if "25.07" in button.text)
    payload = DatePickerCallback.unpack(saturday.callback_data or "")

    assert saturday.text.startswith("🚫")
    assert payload.action == "off"
    assert payload.value == "2026-07-25"


def test_active_date_callback_contains_iso_value_and_fits_telegram_limit() -> None:
    page = build(date(2026, 7, 27))
    button = date_picker_keyboard(page).inline_keyboard[0][0]
    payload = DatePickerCallback.unpack(button.callback_data or "")

    assert payload.action == "pick"
    assert payload.value == "2026-07-27"
    assert len((button.callback_data or "").encode()) <= 64


def test_today_button_selects_today_instead_of_redrawing_same_page() -> None:
    page = build(date(2026, 7, 27))
    today_button = next(
        button
        for row in date_picker_keyboard(page).inline_keyboard
        for button in row
        if "Сегодня" in button.text
    )
    payload = DatePickerCallback.unpack(today_button.callback_data or "")

    assert payload.action == "pick"
    assert payload.value == "2026-07-27"


def test_past_and_beyond_horizon_selections_are_rejected() -> None:
    today = date(2026, 7, 23)

    with pytest.raises(DatePickerValidationError, match="прошла"):
        service.validate_selection(
            today - timedelta(days=1),
            today=today,
            booking_horizon_days=31,
            allow_saturday=True,
            allow_sunday=True,
        )
    with pytest.raises(DatePickerValidationError, match="пределами"):
        service.validate_selection(
            today + timedelta(days=32),
            today=today,
            booking_horizon_days=31,
            allow_saturday=True,
            allow_sunday=True,
        )


def test_disallowed_weekend_cannot_be_selected() -> None:
    with pytest.raises(DatePickerValidationError, match="выходной"):
        service.validate_selection(
            date(2026, 7, 25),
            today=date(2026, 7, 23),
            booking_horizon_days=31,
            allow_saturday=False,
            allow_sunday=False,
        )
