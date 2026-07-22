"""Appointment rendering and Telegram callback size tests."""

from datetime import UTC, datetime
from decimal import Decimal

from app.domain.enums import AppointmentStatus
from app.handlers.admin.appointment_common import render_admin_appointment
from app.keyboards.admin.appointments import AdminAppointmentCallback
from app.keyboards.admin.settings import SettingsCallback, settings_keyboard
from app.keyboards.client.appointments import AppointmentCallback
from app.schemas.appointment import AdminAppointmentView
from app.schemas.settings import BusinessSettingsView


def admin_appointment() -> AdminAppointmentView:
    return AdminAppointmentView(
        id=11,
        service_name="Маникюр <premium>",
        price=Decimal("2500.00"),
        duration_min_minutes=120,
        duration_max_minutes=180,
        status=AppointmentStatus.CONFIRMED,
        start_at=datetime(2026, 7, 23, 7, tzinfo=UTC),
        end_at=datetime(2026, 7, 23, 10, tzinfo=UTC),
        timezone="Europe/Moscow",
        address="Дом <20>",
        map_url="https://example.com/map",
        master_telegram_url="https://t.me/lanrouge",
        can_self_manage=True,
        client_name="Анна & Ко",
        client_phone="+7<999>",
        client_username="anna_test",
    )


def settings_view() -> BusinessSettingsView:
    return BusinessSettingsView(
        business_name="lanrouge nails",
        timezone="Europe/Moscow",
        address="Address",
        map_url="https://example.com/map",
        master_telegram_url="https://t.me/lanrouge",
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

    assert "Маникюр &lt;premium&gt;" in rendered
    assert "Анна &amp; Ко" in rendered
    assert "+7&lt;999&gt;" in rendered


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
