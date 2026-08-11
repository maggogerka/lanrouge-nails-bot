"""Pure formatting and input parsing used by admin handlers."""

from decimal import Decimal

from app.domain.enums import StaffRole
from app.handlers.admin.service_common import (
    parse_positive_minutes,
    parse_price,
    render_service,
)
from app.keyboards.admin.main import ADMIN_SERVICES_TEXT, admin_main_keyboard
from app.keyboards.admin.services import (
    ServiceCallback,
    service_details_keyboard,
    service_list_keyboard,
)
from app.schemas.authorization import StaffContext
from app.schemas.service import ServiceView


def service_view() -> ServiceView:
    return ServiceView(
        id=10,
        name="Консультация <premium>",
        description="Безопасно & красиво",
        price=Decimal("2500.00"),
        duration_min_minutes=120,
        duration_max_minutes=180,
        is_active=True,
    )


def test_service_render_escapes_html_and_formats_snapshot_values() -> None:
    rendered = render_service(service_view())

    assert "Консультация &lt;premium&gt;" in rendered
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


def test_admin_services_button_sends_handler_text_without_hidden_whitespace() -> None:
    context = StaffContext(
        business_id=1,
        staff_member_id=1,
        user_id=1,
        telegram_id=123,
        display_name="Владелец",
        role=StaffRole.OWNER,
        is_bookable=False,
    )
    keyboard = admin_main_keyboard(staff_context=context)
    button_texts = {button.text for row in keyboard.keyboard for button in row}

    assert ADMIN_SERVICES_TEXT == "🛠 Услуги"
    assert ADMIN_SERVICES_TEXT in button_texts


def test_service_list_can_show_and_hide_archived_rows() -> None:
    active = service_view()
    archived = active.model_copy(update={"is_active": False})

    visible = service_list_keyboard([active], include_archived=False)
    with_archive = service_list_keyboard([active, archived], include_archived=True)

    assert any("Показать архив" in button.text for row in visible.inline_keyboard for button in row)
    assert any(
        "Скрыть архив" in button.text for row in with_archive.inline_keyboard for button in row
    )
