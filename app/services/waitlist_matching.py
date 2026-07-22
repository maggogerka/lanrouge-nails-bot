"""Transactional matching when an availability window becomes open."""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.database.models import AvailabilityWindow, BusinessSettings
from app.domain.enums import AvailabilityWindowStatus, WaitlistStatus
from app.repositories.uow import SqlAlchemyUnitOfWork


async def enqueue_waitlist_matches(
    unit_of_work: SqlAlchemyUnitOfWork,
    window: AvailabilityWindow,
    settings: BusinessSettings,
    *,
    now: datetime,
    correlation_id: str | None = None,
) -> int:
    """Queue every eligible request once; booking itself remains first-come-first-served."""

    if window.status is not AvailabilityWindowStatus.OPEN or window.start_at <= now:
        return 0
    local = window.start_at.astimezone(ZoneInfo(settings.timezone))
    duration = int((window.end_at - window.start_at).total_seconds() // 60)
    entries = await unit_of_work.waitlist.list_matching(
        local_date=local.date(),
        local_time=local.time().replace(tzinfo=None),
        window_duration_minutes=duration,
        now=now,
        notified_before=now - timedelta(minutes=settings.waitlist_notification_cooldown_minutes),
    )
    queued = 0
    for entry in entries:
        if await unit_of_work.waitlist.enqueue_match(
            entry_id=entry.id,
            window_id=window.id,
            scheduled_at=now,
        ):
            entry.status = WaitlistStatus.MATCHED
            entry.notified_at = now
            queued += 1
    if queued:
        await unit_of_work.audit.add(
            actor_user_id=None,
            action="waitlist.matches_queued",
            entity_type="availability_window",
            entity_id=str(window.id),
            changes={"match_count": queued},
            correlation_id=correlation_id,
        )
    return queued
