"""Framework-independent appointment lifecycle rules."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.domain.enums import AppointmentStatus
from app.domain.errors import AppointmentStateError, CancellationDeadlineError

CLIENT_CHANGE_BLOCKED_MESSAGE = (
    "До записи осталось меньше 36 часов. Самостоятельная отмена или перенос уже "
    "недоступны. Пожалуйста, напишите мастеру и обсудите обстоятельства лично."
)

ACTIVE_APPOINTMENT_STATUSES = (
    AppointmentStatus.CONFIRMED,
    AppointmentStatus.CLIENT_CONFIRMED,
)


def ensure_active_appointment(status: AppointmentStatus) -> None:
    """Allow client/admin change operations only on a future active booking."""

    if status not in ACTIVE_APPOINTMENT_STATUSES:
        raise AppointmentStateError("Эта запись уже не является активной.")


def ensure_client_change_deadline(
    *,
    start_at: datetime,
    now: datetime,
    deadline_hours: int,
) -> None:
    """Permit self-service at exactly the configured deadline or earlier."""

    if start_at.tzinfo is None or now.tzinfo is None:
        raise ValueError("appointment timestamps must be timezone-aware")
    if start_at - now < timedelta(hours=deadline_hours):
        raise CancellationDeadlineError(CLIENT_CHANGE_BLOCKED_MESSAGE)
