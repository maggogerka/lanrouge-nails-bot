"""Pure parsing and rendering shared by availability handlers."""

from __future__ import annotations

import re
from datetime import date, datetime, time
from html import escape
from zoneinfo import ZoneInfo

from app.domain.enums import AvailabilityWindowStatus
from app.schemas.availability import AvailabilityWindowView

_STATUS_LABELS = {
    AvailabilityWindowStatus.OPEN: "открыто",
    AvailabilityWindowStatus.RESERVED: "зарезервировано",
    AvailabilityWindowStatus.BOOKED: "занято",
    AvailabilityWindowStatus.CLOSED: "закрыто",
    AvailabilityWindowStatus.EXPIRED: "истекло",
}


def parse_local_date(raw: str | None) -> date | None:
    """Parse the administrator-facing DD.MM.YYYY date format."""

    if raw is None:
        return None
    try:
        return datetime.strptime(raw.strip(), "%d.%m.%Y").date()
    except ValueError:
        return None


def parse_local_time(raw: str | None) -> time | None:
    """Parse H:MM/HH:MM and reject 24:00 or malformed wall-clock values."""

    if raw is None:
        return None
    match = re.fullmatch(r"(?P<hour>\d{1,2}):(?P<minute>\d{2})", raw.strip())
    if match is None:
        return None
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    if hour > 23 or minute > 59:
        return None
    return time(hour=hour, minute=minute)


def render_window(window: AvailabilityWindowView) -> str:
    """Render one admin-only window card in the business timezone."""

    zone = ZoneInfo(window.timezone)
    start = window.start_at.astimezone(zone)
    end = window.end_at.astimezone(zone)
    comment = escape(window.admin_comment) if window.admin_comment else "—"
    master = escape(window.master_name or "не указан")
    service = escape(window.service_name or "старое окно без услуги")
    workstation = escape(window.workstation_name or "не назначено")
    return (
        f"<b>Окно ID: <code>{window.id}</code></b>\n"
        f"Дата: {start:%d.%m.%Y}\n"
        f"Время: {start:%H:%M}–{end:%H:%M}\n"
        f"Мастер: {master}\n"
        f"Услуга: {service}\n"
        f"Рабочее место: {workstation}\n"
        f"Статус: {_STATUS_LABELS[window.status]}\n"
        f"Внутренний комментарий: {comment}"
    )
