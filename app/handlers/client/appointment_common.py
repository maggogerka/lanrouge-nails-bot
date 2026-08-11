"""Pure client appointment presentation helpers."""

from html import escape
from zoneinfo import ZoneInfo

from app.handlers.client.booking_common import format_duration_range
from app.schemas.appointment import AppointmentView

_STATUS_LABELS = {
    "confirmed": "подтверждена",
    "client_confirmed": "визит подтверждён",
    "completed": "завершена",
    "cancelled_by_client": "отменена клиентом",
    "cancelled_by_admin": "отменена мастером",
    "no_show": "неявка",
    "rescheduled": "перенесена",
}


def render_appointment(appointment: AppointmentView) -> str:
    local = appointment.start_at.astimezone(ZoneInfo(appointment.timezone))
    duration = format_duration_range(
        appointment.duration_min_minutes,
        appointment.duration_max_minutes,
    )
    return (
        f"<b>{escape(appointment.service_name)}</b>\n"
        f"Дата: {local:%d.%m.%Y}\n"
        f"Время: {local:%H:%M}\n"
        "Продолжительность: "
        f"{duration}\n"
        f"Стоимость: {appointment.price:.2f} ₽\n"
        f"Адрес: {escape(appointment.address)}\n"
        f"Статус: {_STATUS_LABELS[appointment.status.value]}"
    )
