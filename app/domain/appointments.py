"""Framework-independent appointment lifecycle rules."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Final

from app.domain.enums import AppointmentStatus
from app.domain.errors import AppointmentStateError, CancellationDeadlineError

ACTIVE_APPOINTMENT_STATUSES: Final[frozenset[AppointmentStatus]] = frozenset(
    {
        AppointmentStatus.CONFIRMED,
        AppointmentStatus.CLIENT_CONFIRMED,
    }
)

SCHEDULE_OCCUPYING_STATUSES: Final[frozenset[AppointmentStatus]] = frozenset(
    {
        AppointmentStatus.PENDING_PAYMENT,
        AppointmentStatus.PENDING_MANUAL_CONFIRMATION,
        AppointmentStatus.CONFIRMED,
        AppointmentStatus.CLIENT_CONFIRMED,
    }
)

ALLOWED_APPOINTMENT_TRANSITIONS: Final[dict[AppointmentStatus, frozenset[AppointmentStatus]]] = {
    AppointmentStatus.PENDING_PAYMENT: frozenset(
        {
            AppointmentStatus.PENDING_MANUAL_CONFIRMATION,
            AppointmentStatus.CONFIRMED,
            AppointmentStatus.PAYMENT_EXPIRED,
            AppointmentStatus.CANCELLED_BY_CLIENT,
            AppointmentStatus.CANCELLED_BY_ADMIN,
        }
    ),
    AppointmentStatus.PENDING_MANUAL_CONFIRMATION: frozenset(
        {
            AppointmentStatus.CONFIRMED,
            AppointmentStatus.PAYMENT_EXPIRED,
            AppointmentStatus.CANCELLED_BY_CLIENT,
            AppointmentStatus.CANCELLED_BY_ADMIN,
        }
    ),
    AppointmentStatus.CONFIRMED: frozenset(
        {
            AppointmentStatus.CLIENT_CONFIRMED,
            AppointmentStatus.COMPLETED,
            AppointmentStatus.CANCELLED_BY_CLIENT,
            AppointmentStatus.CANCELLED_BY_ADMIN,
            AppointmentStatus.NO_SHOW,
            AppointmentStatus.RESCHEDULED,
            AppointmentStatus.REFUND_PENDING,
        }
    ),
    AppointmentStatus.CLIENT_CONFIRMED: frozenset(
        {
            AppointmentStatus.COMPLETED,
            AppointmentStatus.CANCELLED_BY_CLIENT,
            AppointmentStatus.CANCELLED_BY_ADMIN,
            AppointmentStatus.NO_SHOW,
            AppointmentStatus.RESCHEDULED,
            AppointmentStatus.REFUND_PENDING,
        }
    ),
    AppointmentStatus.COMPLETED: frozenset({AppointmentStatus.REFUND_PENDING}),
    AppointmentStatus.CANCELLED_BY_CLIENT: frozenset({AppointmentStatus.REFUND_PENDING}),
    AppointmentStatus.CANCELLED_BY_ADMIN: frozenset({AppointmentStatus.REFUND_PENDING}),
    AppointmentStatus.NO_SHOW: frozenset({AppointmentStatus.REFUND_PENDING}),
    AppointmentStatus.RESCHEDULED: frozenset({AppointmentStatus.REFUND_PENDING}),
    AppointmentStatus.PAYMENT_EXPIRED: frozenset(),
    AppointmentStatus.REFUND_PENDING: frozenset(
        {
            AppointmentStatus.PARTIALLY_REFUNDED,
            AppointmentStatus.REFUNDED,
        }
    ),
    AppointmentStatus.PARTIALLY_REFUNDED: frozenset(
        {AppointmentStatus.REFUND_PENDING, AppointmentStatus.REFUNDED}
    ),
    AppointmentStatus.REFUNDED: frozenset(),
}


def ensure_appointment_transition(
    current: AppointmentStatus,
    target: AppointmentStatus,
) -> None:
    """Validate every status change through one auditable transition table."""

    if current is target:
        return
    if target not in ALLOWED_APPOINTMENT_TRANSITIONS[current]:
        raise AppointmentStateError(f"Appointment transition is not allowed: {current} -> {target}")


def occupies_schedule(status: AppointmentStatus) -> bool:
    """Match the database exclusion-constraint predicate exactly."""

    return status in SCHEDULE_OCCUPYING_STATUSES


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
        raise CancellationDeadlineError(
            f"До записи осталось меньше {deadline_hours} ч. Самостоятельная отмена уже "
            "недоступна. Пожалуйста, напишите мастеру."
        )


def ensure_client_reschedule_deadline(
    *,
    start_at: datetime,
    now: datetime,
    deadline_hours: int,
) -> None:
    """Apply the independently configured self-service reschedule deadline."""

    if start_at.tzinfo is None or now.tzinfo is None:
        raise ValueError("appointment timestamps must be timezone-aware")
    if start_at - now < timedelta(hours=deadline_hours):
        raise CancellationDeadlineError(
            f"До записи осталось меньше {deadline_hours} ч. Самостоятельный перенос уже "
            "недоступен. Вы можете отменить запись, если срок отмены ещё не наступил, "
            "или написать мастеру."
        )
