"""Pure formatting helpers for the client booking flow."""

from __future__ import annotations

from datetime import date
from html import escape
from zoneinfo import ZoneInfo

from app.schemas.booking import BookingReceipt, BookingWindowView, BusinessInfo
from app.schemas.service import ServiceView


def format_duration_range(minimum: int, maximum: int) -> str:
    """Format a minute range as an approximate human-readable duration."""

    if minimum == maximum:
        return f"примерно {_format_minutes(minimum)}"
    return f"примерно {_format_minutes(minimum)}–{_format_minutes(maximum)}"


def available_dates(windows: list[BookingWindowView]) -> list[date]:
    """Return ordered unique dates in each window's declared business timezone."""

    return sorted(
        {window.start_at.astimezone(ZoneInfo(window.timezone)).date() for window in windows}
    )


def render_booking_confirmation(
    service: ServiceView,
    window: BookingWindowView,
    info: BusinessInfo,
    *,
    client_name: str,
    design_title: str | None = None,
) -> str:
    """Render the pre-commit confirmation without internal window data."""

    local = window.start_at.astimezone(ZoneInfo(window.timezone))
    design_line = f"Дизайн: {escape(design_title)}\n" if design_title else ""
    return (
        "<b>Проверьте запись</b>\n\n"
        f"Услуга: {escape(service.name)}\n"
        f"Дата: {local:%d.%m.%Y}\n"
        f"Время: {local:%H:%M}\n"
        "Продолжительность: "
        f"{format_duration_range(service.duration_min_minutes, service.duration_max_minutes)}\n"
        f"Стоимость: {service.price:.2f} ₽\n"
        f"{design_line}"
        f"Имя: {escape(client_name)}\n"
        f"Адрес: {escape(info.address)}"
    )


def render_booking_receipt(receipt: BookingReceipt) -> str:
    """Render the committed client confirmation."""

    local = receipt.start_at.astimezone(ZoneInfo(receipt.timezone))
    design_line = f"Дизайн: {escape(receipt.design_title)}\n" if receipt.design_title else ""
    return (
        "<b>Запись подтверждена! 💅</b>\n\n"
        f"Услуга: {escape(receipt.service_name)}\n"
        f"Дата: {local:%d.%m.%Y}\n"
        f"Время: {local:%H:%M}\n"
        "Продолжительность: "
        f"{format_duration_range(receipt.duration_min_minutes, receipt.duration_max_minutes)}\n"
        f"Стоимость: {receipt.price:.2f} ₽\n"
        f"{design_line}"
        f"Адрес: {escape(receipt.address)}"
    )


def render_admin_new_booking(receipt: BookingReceipt) -> str:
    """Render a direct administrator notification; this text is never logged."""

    local = receipt.start_at.astimezone(ZoneInfo(receipt.timezone))
    design_line = f"Дизайн: {escape(receipt.design_title)}\n" if receipt.design_title else ""
    return (
        "<b>Новая запись</b>\n"
        f"Запись №{receipt.appointment_id}\n"
        f"Услуга: {escape(receipt.service_name)}\n"
        f"{design_line}"
        f"Дата и время: {local:%d.%m.%Y %H:%M}\n"
        f"Клиентка: {escape(receipt.client_name)}\n"
        f"Телефон: {escape(receipt.phone)}"
    )


def _format_minutes(value: int) -> str:
    hours, minutes = divmod(value, 60)
    if not hours:
        return f"{minutes} мин."
    if not minutes:
        return f"{hours} ч."
    return f"{hours} ч. {minutes} мин."
