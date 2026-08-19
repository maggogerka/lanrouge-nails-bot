"""Readable and bounded administrator CRM appointment history."""

from datetime import UTC, datetime
from decimal import Decimal

from app.domain.enums import AppointmentStatus, PaymentMode, PaymentStatus
from app.handlers.admin.crm import _render_history_item
from app.keyboards.admin.crm import CrmCallback, client_history_keyboard
from app.schemas.crm import ClientAppointmentHistoryView


def history_item() -> ClientAppointmentHistoryView:
    return ClientAppointmentHistoryView(
        id=11,
        status=AppointmentStatus.COMPLETED,
        service_name="Маникюр <комплекс>",
        master_name="Руслана",
        price=Decimal("2700.00"),
        prepayment_amount=Decimal("500.00"),
        currency="RUB",
        payment_mode=PaymentMode.MANUAL,
        start_at=datetime(2026, 8, 19, 10, tzinfo=UTC),
        end_at=datetime(2026, 8, 19, 12, tzinfo=UTC),
        timezone="Europe/Moscow",
        completed_at=datetime(2026, 8, 19, 11, 30, tzinfo=UTC),
        payment_id=31,
        payment_status=PaymentStatus.PARTIALLY_REFUNDED,
        payment_amount=Decimal("500.00"),
        refunded_amount=Decimal("100.00"),
        paid_at=datetime(2026, 8, 18, 10, tzinfo=UTC),
    )


def test_history_item_is_localized_financially_explicit_and_html_safe() -> None:
    text = _render_history_item(history_item())

    assert "19.08.2026" in text
    assert "13:00–15:00" in text
    assert "Маникюр &lt;комплекс&gt;" in text
    assert "Стоимость записи: 2700.00 ₽" in text
    assert "Предоплата #31: 500.00 ₽ · частично возвращена" in text
    assert "Подтверждено в боте: 400.00 ₽" in text
    assert "Возвращено: 100.00 ₽" in text


def test_history_keyboard_has_bounded_navigation_and_back_to_client() -> None:
    keyboard = client_history_keyboard(5, page=2, pages=3)
    callbacks = [
        CrmCallback.unpack(button.callback_data or "")
        for row in keyboard.inline_keyboard
        for button in row
    ]

    assert {(item.action, item.page) for item in callbacks if item.action == "history"} == {
        ("history", 1),
        ("history", 3),
    }
    assert any(item.action == "view" and item.client_id == 5 for item in callbacks)
