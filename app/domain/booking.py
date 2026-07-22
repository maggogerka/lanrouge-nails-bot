"""Pure booking validation and contact normalization."""

from __future__ import annotations

import re
from datetime import datetime

from app.domain.errors import BookingUnavailableError

_PHONE_SEPARATORS = re.compile(r"[\s()\-]")


def normalize_phone(value: str) -> str:
    """Normalize a declared phone to a conservative E.164-like representation."""

    normalized = _PHONE_SEPARATORS.sub("", value.strip())
    if normalized.startswith("8") and len(normalized) == 11:
        normalized = "+7" + normalized[1:]
    elif normalized.isdecimal() and len(normalized) == 10:
        normalized = "+7" + normalized
    elif normalized.startswith("+"):
        pass
    elif normalized.isdecimal():
        normalized = "+" + normalized

    digits = normalized[1:] if normalized.startswith("+") else ""
    if not digits.isdecimal() or not 10 <= len(digits) <= 15:
        raise ValueError("Введите корректный номер телефона, например +7 999 123-45-67.")
    return normalized


def validate_service_fits_window(
    *,
    duration_max_minutes: int,
    start_at: datetime,
    end_at: datetime,
) -> None:
    """Require the maximum advertised service duration to fit the full window."""

    available_seconds = (end_at - start_at).total_seconds()
    if duration_max_minutes * 60 > available_seconds:
        raise BookingUnavailableError("Выбранная услуга не помещается в это окно.")
