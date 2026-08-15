"""Pure client appointment presentation helpers."""

from html import escape
from zoneinfo import ZoneInfo

from app.handlers.client.booking_common import format_duration_range
from app.schemas.appointment import AppointmentView
from app.utils.pricing import format_rub_price

_STATUS_LABELS = {
    "pending_payment": "ожидает оплаты",
    "pending_manual_confirmation": "ожидает проверки предоплаты",
    "confirmed": "подтверждена",
    "client_confirmed": "визит подтверждён",
    "completed": "завершена",
    "cancelled_by_client": "отменена клиентом",
    "cancelled_by_admin": "отменена мастером",
    "no_show": "неявка",
    "rescheduled": "перенесена",
    "payment_expired": "резерв оплаты истёк",
    "refund_pending": "возврат обрабатывается",
    "partially_refunded": "частичный возврат",
    "refunded": "деньги возвращены",
}


def render_appointment(appointment: AppointmentView) -> str:
    local = appointment.start_at.astimezone(ZoneInfo(appointment.timezone))
    duration = format_duration_range(
        appointment.duration_min_minutes,
        appointment.duration_max_minutes,
    )
    master_line = f"Мастер: {escape(appointment.master_name)}\n" if appointment.master_name else ""
    status = _STATUS_LABELS.get(appointment.status.value, appointment.status.value)
    return (
        f"<b>{escape(appointment.service_name)}</b>\n"
        f"{master_line}"
        f"Дата: {local:%d.%m.%Y}\n"
        f"Время: {local:%H:%M}\n"
        "Продолжительность: "
        f"{duration}\n"
        f"Стоимость: {format_rub_price(appointment.price)}\n"
        f"Адрес: {escape(appointment.address)}\n"
        f"Статус: {status}"
    )
