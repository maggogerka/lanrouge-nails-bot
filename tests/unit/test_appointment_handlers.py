"""Appointment rendering and Telegram callback size tests."""

from datetime import UTC, datetime
from decimal import Decimal

from app.domain.enums import AppointmentStatus
from app.handlers.admin.appointment_common import render_admin_appointment
from app.keyboards.admin.appointments import (
    AdminAppointmentCallback,
    admin_appointment_details_keyboard,
    admin_appointment_list_keyboard,
)
from app.keyboards.admin.settings import SettingsCallback, settings_keyboard
from app.keyboards.client.appointments import AppointmentCallback
from app.schemas.appointment import AdminAppointmentView
from app.schemas.settings import BusinessSettingsView


def admin_appointment() -> AdminAppointmentView:
    return AdminAppointmentView(
        id=11,
        service_name="Консультация <premium>",
        price=Decimal("2500.00"),
        duration_min_minutes=120,
        duration_max_minutes=180,
        status=AppointmentStatus.CONFIRMED,
        start_at=datetime(2026, 7, 23, 7, tzinfo=UTC),
        end_at=datetime(2026, 7, 23, 10, tzinfo=UTC),
        timezone="Europe/Moscow",
        address="Дом <20>",
        map_url="https://example.com/map",
        master_telegram_url="https://t.me/example_studio",
        can_self_manage=True,
        client_name="Анна & Ко",
        client_phone="+7<999>",
        client_username="anna_test",
        client_telegram_id=123456,
    )


def settings_view() -> BusinessSettingsView:
    return BusinessSettingsView(
        business_name="Example Studio",
        timezone="Europe/Moscow",
        address="Address",
        map_url="https://example.com/map",
        master_telegram_url="https://t.me/example_studio",
        booking_horizon_days=31,
        cancellation_deadline_hours=36,
        max_appointments_per_day=2,
        default_window_duration_minutes=210,
        minimum_gap_minutes=60,
        allow_saturday=False,
        allow_sunday=False,
        reminder_offsets_minutes=[1440, 180, 60],
        version=1,
    )


def test_admin_appointment_render_escapes_contact_values() -> None:
    rendered = render_admin_appointment(admin_appointment())

    assert "Консультация &lt;premium&gt;" in rendered
    assert "Анна &amp; Ко" in rendered
    assert "+7&lt;999&gt;" in rendered


def test_admin_can_open_client_chat_even_without_username() -> None:
    appointment = admin_appointment().model_copy(update={"client_username": None})
    keyboard = admin_appointment_details_keyboard(appointment)
    contact = keyboard.inline_keyboard[0][0]

    assert contact.text == "💬 Написать клиенту"
    assert str(contact.url) == "tg://user?id=123456"


def test_appointment_and_settings_callbacks_fit_telegram_limit() -> None:
    client = AppointmentCallback(
        action="rconfirm",
        appointment_id=9_223_372_036_854_775_807,
        object_id=9_223_372_036_854_775_807,
    ).pack()
    admin = AdminAppointmentCallback(
        action="rconfirm",
        appointment_id=9_223_372_036_854_775_807,
        object_id=9_223_372_036_854_775_807,
    ).pack()
    setting = SettingsCallback(action="default_window_duration_minutes").pack()

    assert len(client.encode()) <= 64
    assert len(admin.encode()) <= 64
    assert len(setting.encode()) <= 64
    assert settings_keyboard(settings_view()).inline_keyboard


def test_admin_upcoming_keyboard_is_grouped_as_calendar_agenda() -> None:
    first = admin_appointment()
    second = first.model_copy(
        update={
            "id": 12,
            "start_at": datetime(2026, 7, 24, 8, tzinfo=UTC),
            "end_at": datetime(2026, 7, 24, 9, tzinfo=UTC),
            "client_name": "Мария",
        }
    )

    keyboard = admin_appointment_list_keyboard([second, first], list_action="upcoming")
    labels = [button.text for row in keyboard.inline_keyboard for button in row]

    assert labels[0] == "📅 Чт, 23 июля · 1 запись"
    assert "10:00 · Анна & Ко · Консультация <premium>" in labels[1]
    assert labels[2] == "📅 Пт, 24 июля · 1 запись"
    assert labels[-1] == "🔄 Обновить календарь"
