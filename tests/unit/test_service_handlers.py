"""Pure formatting and input parsing used by admin handlers."""

from decimal import Decimal

from app.handlers.admin.service_common import (
    parse_positive_minutes,
    parse_price,
    render_service,
)
from app.keyboards.admin.services import ServiceCallback, service_details_keyboard
from app.schemas.service import ServiceView


def service_view() -> ServiceView:
    return ServiceView(
        id=10,
        name="Маникюр <premium>",
        description="Безопасно & красиво",
        price=Decimal("2500.00"),
        duration_min_minutes=120,
        duration_max_minutes=180,
        is_active=True,
    )


def test_service_render_escapes_html_and_formats_snapshot_values() -> None:
    rendered = render_service(service_view())

    assert "Маникюр &lt;premium&gt;" in rendered
    assert "Безопасно &amp; красиво" in rendered
    assert "2500.00 ₽" in rendered
    assert "120–180 мин." in rendered


def test_price_and_minutes_parsers_are_strict() -> None:
    assert parse_price("2 500,50") == Decimal("2500.50")
    assert parse_price("not-a-price") is None
    assert parse_positive_minutes("180") == 180
    assert parse_positive_minutes("0") is None
    assert parse_positive_minutes("1441") is None


def test_service_callbacks_fit_telegram_limit() -> None:
    callback = ServiceCallback(
        action="edit_description",
        service_id=9_223_372_036_854_775_807,
    ).pack()

    assert len(callback.encode()) <= 64
    assert service_details_keyboard(service_view()).inline_keyboard
