"""Pure scheduling rules for persistent appointment reminders."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.domain.enums import NotificationType


@dataclass(frozen=True, slots=True)
class ReminderSchedule:
    """One future reminder independent of persistence."""

    recipient_user_id: int
    notification_type: NotificationType
    offset_minutes: int
    scheduled_at: datetime


def future_reminder_schedules(
    *,
    start_at: datetime,
    now: datetime,
    offsets_minutes: list[int],
    client_user_id: int,
    admin_user_ids: list[int],
) -> list[ReminderSchedule]:
    """Build only reminders whose scheduled instant is strictly in the future."""

    if start_at.tzinfo is None or now.tzinfo is None:
        raise ValueError("reminder timestamps must be timezone-aware")

    recipients = [
        (client_user_id, NotificationType.CLIENT_REMINDER),
        *((admin_id, NotificationType.ADMIN_REMINDER) for admin_id in admin_user_ids),
    ]
    schedules: list[ReminderSchedule] = []
    for offset in offsets_minutes:
        if offset <= 0:
            raise ValueError("reminder offsets must be positive")
        scheduled_at = start_at - timedelta(minutes=offset)
        if scheduled_at <= now:
            continue
        schedules.extend(
            ReminderSchedule(
                recipient_user_id=recipient_id,
                notification_type=notification_type,
                offset_minutes=offset,
                scheduled_at=scheduled_at,
            )
            for recipient_id, notification_type in recipients
        )
    return schedules
