"""Pure window handler formatting and callback tests."""

from datetime import UTC, datetime

from app.domain.enums import AvailabilityWindowStatus
from app.handlers.admin.window_common import parse_local_date, parse_local_time, render_window
from app.keyboards.admin.windows import WindowCallback, window_details_keyboard
from app.schemas.availability import AvailabilityWindowView


def window_view() -> AvailabilityWindowView:
    return AvailabilityWindowView(
        id=42,
        start_at=datetime(2026, 7, 23, 7, tzinfo=UTC),
        end_at=datetime(2026, 7, 23, 10, 30, tzinfo=UTC),
        status=AvailabilityWindowStatus.OPEN,
        admin_comment="Взять <палитру> & лампу",
        timezone="Europe/Moscow",
    )


def test_window_render_converts_timezone_and_escapes_comment() -> None:
    rendered = render_window(window_view())

    assert "23.07.2026" in rendered
    assert "10:00–13:30" in rendered
    assert "&lt;палитру&gt; &amp; лампу" in rendered


def test_window_date_and_time_parsers_are_strict() -> None:
    assert parse_local_date("23.07.2026").isoformat() == "2026-07-23"  # type: ignore[union-attr]
    assert parse_local_date("2026-07-23") is None
    assert parse_local_time("09:05").isoformat() == "09:05:00"  # type: ignore[union-attr]
    assert parse_local_time("25:00") is None


def test_window_callbacks_fit_telegram_limit() -> None:
    callback = WindowCallback(
        action="delete_confirm",
        window_id=9_223_372_036_854_775_807,
    ).pack()

    assert len(callback.encode()) <= 64
    assert window_details_keyboard(window_view()).inline_keyboard
