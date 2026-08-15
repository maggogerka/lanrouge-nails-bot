"""Pure administrator appointment rendering."""

from html import escape
from zoneinfo import ZoneInfo

from app.handlers.client.appointment_common import render_appointment
from app.schemas.appointment import AdminAppointmentView


def render_admin_appointment(appointment: AdminAppointmentView) -> str:
    username = f"@{escape(appointment.client_username)}" if appointment.client_username else "—"
    telegram_id = str(appointment.client_telegram_id) if appointment.client_telegram_id else "—"
    phone = escape(appointment.client_phone) if appointment.client_phone else "—"
    comment = escape(appointment.client_comment) if appointment.client_comment else "—"
    workstation = escape(appointment.workstation_name) if appointment.workstation_name else "—"
    payment_mode = {
        "disabled": "без предоплаты",
        "manual": "ручная предоплата",
        "yookassa": "ЮKassa",
    }.get(appointment.payment_mode.value, appointment.payment_mode.value)
    expiry = "—"
    if appointment.reservation_expires_at is not None:
        expiry = appointment.reservation_expires_at.astimezone(
            ZoneInfo(appointment.timezone)
        ).strftime("%d.%m.%Y %H:%M")
    return (
        f"<b>Запись №{appointment.id}</b>\n\n"
        + render_appointment(appointment)
        + "\n\n"
        + f"Клиент: {escape(appointment.client_name)}\n"
        + f"Telegram: {username}\n"
        + f"Telegram ID: <code>{telegram_id}</code>\n"
        + f"Телефон: {phone}\n"
        + f"Рабочее место: {workstation}\n"
        + f"Комментарий клиента: {comment}\n"
        + f"Оплата: {payment_mode}\n"
        + f"Предоплата: {appointment.prepayment_amount:.2f} ₽\n"
        + f"Резерв до: {expiry}"
    )
