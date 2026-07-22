"""Campaign persistence, audience snapshots and concurrent recipient claims."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Broadcast,
    BroadcastMedia,
    BroadcastRecipient,
    MarketingEvent,
    User,
)
from app.domain.enums import BroadcastRecipientStatus, BroadcastStatus


class BroadcastRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, broadcast_id: int, *, for_update: bool = False) -> Broadcast | None:
        statement = select(Broadcast).where(Broadcast.id == broadcast_id)
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def add(self, broadcast: Broadcast) -> Broadcast:
        self._session.add(broadcast)
        await self._session.flush()
        return broadcast

    async def list_page(
        self,
        *,
        status: BroadcastStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Broadcast], int]:
        filters = []
        if status is not None:
            filters.append(Broadcast.status == status)
        rows = (
            select(Broadcast)
            .where(*filters)
            .order_by(Broadcast.created_at.desc(), Broadcast.id.desc())
            .limit(limit)
            .offset(offset)
        )
        count = select(func.count(Broadcast.id)).where(*filters)
        return list((await self._session.scalars(rows)).all()), int(
            (await self._session.scalar(count)) or 0
        )

    async def add_media(self, media: list[BroadcastMedia]) -> None:
        self._session.add_all(media)
        await self._session.flush()

    async def list_media(self, broadcast_id: int) -> list[BroadcastMedia]:
        result = await self._session.scalars(
            select(BroadcastMedia)
            .where(BroadcastMedia.broadcast_id == broadcast_id)
            .order_by(BroadcastMedia.position, BroadcastMedia.id)
        )
        return list(result.all())

    async def freeze_recipients(
        self,
        *,
        broadcast_id: int,
        user_ids: list[int],
        scheduled_at: datetime,
    ) -> int:
        if not user_ids:
            return 0
        values = [
            {
                "broadcast_id": broadcast_id,
                "user_id": user_id,
                "scheduled_at": scheduled_at,
                "available_at": scheduled_at,
            }
            for user_id in user_ids
        ]
        result = await self._session.execute(
            insert(BroadcastRecipient)
            .values(values)
            .on_conflict_do_nothing(index_elements=["broadcast_id", "user_id"])
            .returning(BroadcastRecipient.id)
        )
        return len(result.scalars().all())

    async def list_subscribed_user_ids(self) -> list[int]:
        result = await self._session.scalars(
            select(User.id)
            .where(
                User.marketing_consent_at.is_not(None),
                User.marketing_unsubscribed_at.is_(None),
                User.is_blocked.is_(False),
            )
            .order_by(User.id)
        )
        return list(result.all())

    async def claim_due_recipients(
        self,
        *,
        now: datetime,
        worker_id: str,
        limit: int,
    ) -> list[BroadcastRecipient]:
        result = await self._session.scalars(
            select(BroadcastRecipient)
            .where(
                BroadcastRecipient.status.in_(
                    (BroadcastRecipientStatus.PENDING, BroadcastRecipientStatus.RETRY)
                ),
                BroadcastRecipient.available_at <= now,
            )
            .order_by(BroadcastRecipient.available_at, BroadcastRecipient.id)
            .with_for_update(skip_locked=True)
            .limit(limit)
        )
        recipients = list(result.all())
        for recipient in recipients:
            recipient.status = BroadcastRecipientStatus.PROCESSING
            recipient.locked_at = now
            recipient.locked_by = worker_id
            recipient.attempts += 1
        await self._session.flush()
        return recipients

    async def add_event(self, event: MarketingEvent) -> MarketingEvent:
        self._session.add(event)
        await self._session.flush()
        return event
