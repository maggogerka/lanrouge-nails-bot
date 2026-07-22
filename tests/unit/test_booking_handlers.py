"""Booking presentation and callback safety tests."""

from datetime import UTC, datetime
from decimal import Decimal

from app.handlers.client.booking_common import (
    format_duration_range,
    render_booking_confirmation,
)
from app.keyboards.client.booking import BookingCallback, confirmation_keyboard
from app.schemas.booking import BookingWindowView, BusinessInfo
from app.schemas.service import ServiceView


def test_confirmation_uses_local_time_and_escapes_client_values() -> None:
    service = ServiceView(
        id=3,
        name="Маникюр <premium>",
        description=None,
        price=Decimal("2500.00"),
        duration_min_minutes=120,
        duration_max_minutes=180,
        is_active=True,
    )
    window = BookingWindowView(
        id=7,
        start_at=datetime(2026, 7, 23, 7, tzinfo=UTC),
        end_at=datetime(2026, 7, 23, 10, 30, tzinfo=UTC),
        timezone="Europe/Moscow",
    )
    info = BusinessInfo(
        business_name="lanrouge nails",
        address="Дом <20>",
        map_url="https://example.com/map",
        master_telegram_url="https://t.me/lanrouge",
    )

    rendered = render_booking_confirmation(
        service,
        window,
        info,
        client_name="Анна & Ко",
    )

    assert "Маникюр &lt;premium&gt;" in rendered
    assert "23.07.2026" in rendered
    assert "10:00" in rendered
    assert "Анна &amp; Ко" in rendered
    assert "Дом &lt;20&gt;" in rendered
    assert format_duration_range(120, 180) == "примерно 2 ч.–3 ч."


def test_booking_callbacks_fit_telegram_limit() -> None:
    callback = BookingCallback(
        action="window",
        object_id=9_223_372_036_854_775_807,
    ).pack()

    assert len(callback.encode()) <= 64
    assert confirmation_keyboard().inline_keyboard
