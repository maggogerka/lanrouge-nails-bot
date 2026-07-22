"""Safe reminder text and inline markup construction."""

from __future__ import annotations

from html import escape
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.domain.enums import NotificationType
from app.keyboards.client.reminders import confirm_visit_keyboard
from app.schemas.notification import NotificationDelivery


def render_reminder(delivery: NotificationDelivery) -> str:
    local = delivery.start_at.astimezone(ZoneInfo(delivery.timezone))
    if delivery.notification_type is NotificationType.CLIENT_REMINDER:
        return (
            "<b>Напоминание о записи 💅</b>\n"
            f"Услуга: {escape(delivery.service_name)}\n"
            f"Дата и время: {local:%d.%m.%Y %H:%M}\n"
            f"Адрес: {escape(delivery.address)}"
        )
    phone = escape(delivery.client_phone) if delivery.client_phone else "—"
    return (
        "<b>Ближайшая запись</b>\n"
        f"Услуга: {escape(delivery.service_name)}\n"
        f"Дата и время: {local:%d.%m.%Y %H:%M}\n"
        f"Клиентка: {escape(delivery.client_name)}\n"
        f"Телефон: {phone}"
    )


def reminder_keyboard(delivery: NotificationDelivery) -> InlineKeyboardMarkup:
    if (
        delivery.notification_type is NotificationType.CLIENT_REMINDER
        and delivery.offset_minutes == 1440
    ):
        return confirm_visit_keyboard(delivery.appointment_id)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📍 Открыть на карте", url=delivery.map_url)],
            [
                InlineKeyboardButton(
                    text="Написать мастеру",
                    url=delivery.master_telegram_url,
                )
            ],
        ]
    )
