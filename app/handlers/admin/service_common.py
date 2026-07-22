"""Pure parsing and rendering shared by service catalog handlers."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from html import escape

from aiogram.types import User as TelegramUser

from app.schemas.service import AdminActor, ServiceView

DURATION_RANGE = re.compile(r"^\s*(\d{1,4})\s*[-–—]\s*(\d{1,4})\s*$")


def actor_from_telegram(user: TelegramUser) -> AdminActor:
    """Copy non-sensitive actor identity into an application DTO."""

    return AdminActor(
        telegram_id=user.id,
        username=user.username,
        first_name=user.first_name,
        last_name=user.last_name,
    )


def render_service(service: ServiceView) -> str:
    """Render escaped service details for HTML parse mode."""

    status = "активна" if service.is_active else "скрыта"
    description = escape(service.description) if service.description else "—"
    return (
        f"<b>{escape(service.name)}</b>\n"
        f"Статус: {status}\n"
        f"Описание: {description}\n"
        f"Стоимость: {service.price:.2f} ₽\n"
        "Продолжительность: "
        f"{service.duration_min_minutes}–{service.duration_max_minutes} мин."
    )


def parse_price(raw: str | None) -> Decimal | None:
    """Parse a human-entered RUB amount without floating-point conversion."""

    if raw is None:
        return None
    normalized = raw.replace(" ", "").replace(",", ".")
    try:
        return Decimal(normalized)
    except InvalidOperation:
        return None


def parse_positive_minutes(raw: str | None) -> int | None:
    """Parse the supported positive minute range."""

    if raw is None or not raw.strip().isdecimal():
        return None
    value = int(raw.strip())
    return value if 0 < value <= 24 * 60 else None
