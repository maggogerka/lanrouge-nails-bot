"""Pure administrator appointment rendering."""

from html import escape

from app.handlers.client.appointment_common import render_appointment
from app.schemas.appointment import AdminAppointmentView


def render_admin_appointment(appointment: AdminAppointmentView) -> str:
    username = f"@{escape(appointment.client_username)}" if appointment.client_username else "—"
    phone = escape(appointment.client_phone) if appointment.client_phone else "—"
    return (
        render_appointment(appointment)
        + "\n\n"
        + f"Клиентка: {escape(appointment.client_name)}\n"
        + f"Telegram: {username}\n"
        + f"Телефон: {phone}"
    )
