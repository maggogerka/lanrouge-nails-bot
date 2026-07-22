"""Pure parsing and rendering shared by availability handlers."""

from __future__ import annotations

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
    """Parse a strict 24-hour HH:MM wall-clock time."""

    if raw is None:
        return None
    try:
        return datetime.strptime(raw.strip(), "%H:%M").time()
    except ValueError:
        return None


def render_window(window: AvailabilityWindowView) -> str:
    """Render one admin-only window card in the business timezone."""

    zone = ZoneInfo(window.timezone)
    start = window.start_at.astimezone(zone)
    end = window.end_at.astimezone(zone)
    comment = escape(window.admin_comment) if window.admin_comment else "—"
    return (
        f"<b>{start:%d.%m.%Y}</b>\n"
        f"Время: {start:%H:%M}–{end:%H:%M}\n"
        f"Статус: {_STATUS_LABELS[window.status]}\n"
        f"Внутренний комментарий: {comment}"
    )
