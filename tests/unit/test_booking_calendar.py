"""Client booking calendar marks only dates with live windows."""

from datetime import date

from app.keyboards.client.booking import BookingDateCallback, booking_date_calendar_keyboard
from app.services.date_picker_service import DatePickerService


def test_booking_calendar_disables_dates_without_windows() -> None:
    page = DatePickerService().build_page(
        today=date(2026, 8, 14),
        requested_start=date(2026, 8, 14),
        booking_horizon_days=4,
        allow_saturday=True,
        allow_sunday=True,
        page_size=5,
    )
    available = {date(2026, 8, 15), date(2026, 8, 17)}

    keyboard = booking_date_calendar_keyboard(
        page,
        available,
        back_action="back_services",
    )
    callbacks = [
        BookingDateCallback.unpack(button.callback_data or "")
        for row in keyboard.inline_keyboard[:2]
        for button in row
    ]

    assert [value.action for value in callbacks] == ["off", "pick", "off", "pick", "off"]
    assert all(
        len((button.callback_data or "").encode()) <= 64
        for row in keyboard.inline_keyboard
        for button in row
    )
