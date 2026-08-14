"""Safe reminder text and inline markup construction."""

from __future__ import annotations

from html import escape
from zoneinfo import ZoneInfo

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.domain.enums import NotificationType
from app.keyboards.client.payments import manual_payment_report_button
from app.keyboards.client.reminders import confirm_visit_keyboard
from app.keyboards.client.repeat_booking import repeat_reminder_keyboard
from app.keyboards.client.reviews import review_request_keyboard
from app.schemas.notification import NotificationDelivery


def render_reminder(delivery: NotificationDelivery) -> str:
    local = delivery.start_at.astimezone(ZoneInfo(delivery.timezone))
    if delivery.notification_type is NotificationType.PAYMENT_DUE_CLIENT:
        return (
            "<b>Напоминание о предоплате</b>\n"
            f"Услуга: {escape(delivery.service_name)}\n"
            "Если перевод уже выполнен, нажмите «Я оплатил»."
        )
    if delivery.notification_type is NotificationType.PAYMENT_REVIEW_STAFF:
        return (
            "<b>Предоплата ожидает проверки</b>\n"
            f"Запись №{delivery.appointment_id} · {escape(delivery.service_name)}.\n"
            "Откройте раздел «Предоплаты» в панели управления."
        )
    if delivery.notification_type is NotificationType.REVIEW_REQUEST:
        return (
            "<b>Спасибо за визит!</b>\n"
            "Будем рады узнать, как всё прошло. Оценка займёт меньше минуты."
        )
    if delivery.notification_type is NotificationType.REPEAT_BOOKING_REMINDER:
        return (
            "<b>Возможно, пришло время записаться снова</b>\n"
            f"В прошлый раз вы выбирали: {escape(delivery.service_name)}.\n"
            "Посмотреть новые свободные окна?"
        )
    if delivery.notification_type is NotificationType.CLIENT_REMINDER:
        return (
            "<b>Напоминание о записи</b>\n"
            f"Услуга: {escape(delivery.service_name)}\n"
            f"Дата и время: {local:%d.%m.%Y %H:%M}\n"
            f"Адрес: {escape(delivery.address)}"
        )
    phone = escape(delivery.client_phone) if delivery.client_phone else "—"
    return (
        "<b>Ближайшая запись</b>\n"
        f"Услуга: {escape(delivery.service_name)}\n"
        f"Дата и время: {local:%d.%m.%Y %H:%M}\n"
        f"Клиент: {escape(delivery.client_name)}\n"
        f"Телефон: {phone}"
    )


def reminder_keyboard(delivery: NotificationDelivery) -> InlineKeyboardMarkup:
    if (
        delivery.notification_type is NotificationType.PAYMENT_DUE_CLIENT
        and delivery.payment_id is not None
    ):
        return InlineKeyboardMarkup(
            inline_keyboard=[[manual_payment_report_button(delivery.payment_id)]]
        )
    if delivery.notification_type is NotificationType.PAYMENT_REVIEW_STAFF:
        return InlineKeyboardMarkup(inline_keyboard=[])
    if delivery.notification_type is NotificationType.REVIEW_REQUEST:
        return review_request_keyboard(delivery.appointment_id)
    if delivery.notification_type is NotificationType.REPEAT_BOOKING_REMINDER:
        return repeat_reminder_keyboard()
    if (
        delivery.notification_type is NotificationType.CLIENT_REMINDER
        and delivery.offset_minutes == 1440
    ):
        return confirm_visit_keyboard(delivery.appointment_id)
    rows: list[list[InlineKeyboardButton]] = []
    if delivery.map_url:
        rows.append([InlineKeyboardButton(text="📍 Открыть на карте", url=delivery.map_url)])
    if delivery.master_telegram_url:
        rows.append(
            [InlineKeyboardButton(text="💬 Написать мастеру", url=delivery.master_telegram_url)]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)
