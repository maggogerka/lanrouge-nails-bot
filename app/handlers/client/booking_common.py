"""Pure formatting helpers for the client booking flow."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from html import escape
from zoneinfo import ZoneInfo

from app.domain.enums import AppointmentStatus, PaymentMode
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
    reference_media_count: int = 0,
) -> str:
    """Render the pre-commit confirmation without internal window data."""

    local = window.start_at.astimezone(ZoneInfo(window.timezone))
    master_line = f"Мастер: {escape(window.master_name)}\n" if window.master_name else ""
    design_line = f"Дизайн: {escape(design_title)}\n" if design_title else ""
    reference_line = (
        f"Фотографии-референсы: {reference_media_count}\n" if reference_media_count else ""
    )
    price = window.price if window.price is not None else service.price
    duration_min = window.duration_min_minutes or service.duration_min_minutes
    duration_max = window.duration_max_minutes or service.duration_max_minutes
    prepayment_line = (
        f"Предоплата: {window.prepayment_amount:.2f} ₽\n"
        if window.prepayment_amount is not None and window.prepayment_amount > 0
        else ""
    )
    return (
        "<b>Проверьте запись</b>\n\n"
        f"Услуга: {escape(service.name)}\n"
        f"{master_line}"
        f"Дата: {local:%d.%m.%Y}\n"
        f"Время: {local:%H:%M}\n"
        "Продолжительность: "
        f"{format_duration_range(duration_min, duration_max)}\n"
        f"Стоимость: {price:.2f} ₽\n"
        f"{prepayment_line}"
        f"{design_line}"
        f"{reference_line}"
        f"Имя: {escape(client_name)}\n"
        f"Адрес: {escape(info.address)}"
    )


def render_booking_receipt(receipt: BookingReceipt) -> str:
    """Render the committed client confirmation."""

    local = receipt.start_at.astimezone(ZoneInfo(receipt.timezone))
    master_line = f"Мастер: {escape(receipt.master_name)}\n" if receipt.master_name else ""
    design_line = f"Дизайн: {escape(receipt.design_title)}\n" if receipt.design_title else ""
    payment_amount = receipt.payment_amount or Decimal("0.00")
    payment_currency = escape(receipt.payment_currency or "RUB")
    if receipt.appointment_status is AppointmentStatus.CONFIRMED:
        heading = "<b>Запись подтверждена! 💅</b>"
        payment_block = ""
    elif receipt.payment_mode is PaymentMode.MANUAL:
        heading = "<b>Время зарезервировано — ожидаем подтверждение оплаты</b>"
        instructions = escape(receipt.manual_payment_instructions or "")
        payment_block = (
            f"\nК оплате: {payment_amount:.2f} {payment_currency}\nИнструкция: {instructions}\n"
        )
    else:
        heading = "<b>Время зарезервировано — завершите оплату</b>"
        payment_block = (
            f"\nК оплате: {payment_amount:.2f} {payment_currency}\n"
            "Используйте кнопку «Перейти к оплате» ниже.\n"
        )
    expiry_line = ""
    if receipt.reservation_expires_at is not None:
        expiry = receipt.reservation_expires_at.astimezone(ZoneInfo(receipt.timezone))
        expiry_line = f"Резерв действует до {expiry:%d.%m.%Y %H:%M}.\n"
    return (
        f"{heading}\n\n"
        f"Услуга: {escape(receipt.service_name)}\n"
        f"{master_line}"
        f"Дата: {local:%d.%m.%Y}\n"
        f"Время: {local:%H:%M}\n"
        "Продолжительность: "
        f"{format_duration_range(receipt.duration_min_minutes, receipt.duration_max_minutes)}\n"
        f"Стоимость: {receipt.price:.2f} ₽\n"
        f"{payment_block}"
        f"{expiry_line}"
        f"{design_line}"
        f"Адрес: {escape(receipt.address)}"
    )


def render_admin_new_booking(receipt: BookingReceipt) -> str:
    """Render a direct administrator notification; this text is never logged."""

    local = receipt.start_at.astimezone(ZoneInfo(receipt.timezone))
    master_line = f"Мастер: {escape(receipt.master_name)}\n" if receipt.master_name else ""
    design_line = f"Дизайн: {escape(receipt.design_title)}\n" if receipt.design_title else ""
    status_line = f"Статус: {receipt.appointment_status.value}\n"
    payment_line = (
        f"Оплата: #{receipt.payment_id}, {receipt.payment_status.value}\n"
        if receipt.payment_id is not None and receipt.payment_status is not None
        else ""
    )
    return (
        "<b>Новая запись</b>\n"
        f"Запись №{receipt.appointment_id}\n"
        f"Услуга: {escape(receipt.service_name)}\n"
        f"{master_line}"
        f"{design_line}"
        f"{status_line}"
        f"{payment_line}"
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
