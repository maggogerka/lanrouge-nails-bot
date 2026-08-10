"""Campaign persistence, audience snapshots and concurrent recipient claims."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import and_, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Appointment,
    AvailabilityWindow,
    Broadcast,
    BroadcastMedia,
    BroadcastRecipient,
    BusinessClient,
    MarketingEvent,
    User,
    UserClientTag,
)
from app.domain.enums import (
    AppointmentStatus,
    BroadcastAudienceType,
    BroadcastRecipientStatus,
    BroadcastStatus,
)
from app.repositories.scoped import TenantScopedRepository


class BroadcastRepository(TenantScopedRepository):
    def __init__(self, session: AsyncSession, business_id: int) -> None:
        super().__init__(session, business_id)

    async def get(self, broadcast_id: int, *, for_update: bool = False) -> Broadcast | None:
        statement = select(Broadcast).where(
            Broadcast.id == broadcast_id,
            Broadcast.business_id == self.business_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def add(self, broadcast: Broadcast) -> Broadcast:
        self._require_business(broadcast.business_id)
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
        filters = [Broadcast.business_id == self.business_id]
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
        broadcast_ids = {item.broadcast_id for item in media}
        if broadcast_ids:
            allowed = set(
                (
                    await self._session.scalars(
                        select(Broadcast.id).where(
                            Broadcast.id.in_(broadcast_ids),
                            Broadcast.business_id == self.business_id,
                        )
                    )
                ).all()
            )
            if allowed != broadcast_ids:
                raise ValueError("broadcast media parent belongs to another business")
        self._session.add_all(media)
        await self._session.flush()

    async def list_media(self, broadcast_id: int) -> list[BroadcastMedia]:
        result = await self._session.scalars(
            select(BroadcastMedia)
            .join(Broadcast, Broadcast.id == BroadcastMedia.broadcast_id)
            .where(
                BroadcastMedia.broadcast_id == broadcast_id,
                Broadcast.business_id == self.business_id,
            )
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
        broadcast_exists = await self._session.scalar(
            select(Broadcast.id).where(
                Broadcast.id == broadcast_id,
                Broadcast.business_id == self.business_id,
            )
        )
        eligible_ids = set(
            (
                await self._session.scalars(
                    select(BusinessClient.user_id).where(
                        BusinessClient.business_id == self.business_id,
                        BusinessClient.user_id.in_(user_ids),
                        BusinessClient.is_active.is_(True),
                        BusinessClient.anonymized_at.is_(None),
                    )
                )
            ).all()
        )
        if broadcast_exists is None or eligible_ids != set(user_ids):
            raise ValueError("broadcast or recipient belongs to another business")
        values = [
            {
                "business_id": self.business_id,
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

    async def resolve_audience_user_ids(
        self,
        *,
        audience_type: BroadcastAudienceType,
        parameters: dict[str, object],
        now: datetime,
    ) -> list[int]:
        filters = [
            BusinessClient.business_id == self.business_id,
            BusinessClient.is_active.is_(True),
            BusinessClient.anonymized_at.is_(None),
            User.marketing_consent_at.is_not(None),
            User.marketing_unsubscribed_at.is_(None),
            User.is_blocked.is_(False),
            User.is_self_booking_blocked.is_(False),
        ]
        completed_visit = exists(
            select(Appointment.id).where(
                Appointment.client_id == User.id,
                Appointment.business_id == self.business_id,
                Appointment.status == AppointmentStatus.COMPLETED,
            )
        )
        future_booking = exists(
            select(Appointment.id)
            .join(AvailabilityWindow, AvailabilityWindow.id == Appointment.window_id)
            .where(
                Appointment.client_id == User.id,
                Appointment.business_id == self.business_id,
                Appointment.status.in_(
                    (AppointmentStatus.CONFIRMED, AppointmentStatus.CLIENT_CONFIRMED)
                ),
                AvailabilityWindow.start_at > now,
                AvailabilityWindow.business_id == self.business_id,
            )
        )
        if bool(parameters.get("completed_only")):
            filters.append(completed_visit)
        if bool(parameters.get("without_future_booking")):
            filters.append(~future_booking)
        if audience_type is BroadcastAudienceType.CLIENT_TAG:
            tag_id = self._int_parameter(parameters.get("tag_id"), default=0)
            filters.append(
                exists(
                    select(UserClientTag.user_id).where(
                        UserClientTag.user_id == User.id,
                        UserClientTag.business_id == self.business_id,
                        UserClientTag.tag_id == tag_id,
                    )
                )
            )
        elif audience_type is BroadcastAudienceType.SERVICE_HISTORY:
            service_id = self._int_parameter(parameters.get("service_id"), default=0)
            filters.append(
                exists(
                    select(Appointment.id).where(
                        Appointment.client_id == User.id,
                        Appointment.business_id == self.business_id,
                        Appointment.service_id == service_id,
                        Appointment.status == AppointmentStatus.COMPLETED,
                    )
                )
            )
        elif audience_type is BroadcastAudienceType.INACTIVE_DAYS:
            days = max(1, self._int_parameter(parameters.get("days"), default=30))
            cutoff = now - timedelta(days=days)
            filters.extend(
                [
                    completed_visit,
                    ~exists(
                        select(Appointment.id).where(
                            Appointment.client_id == User.id,
                            Appointment.business_id == self.business_id,
                            Appointment.status == AppointmentStatus.COMPLETED,
                            Appointment.completed_at > cutoff,
                        )
                    ),
                ]
            )
        elif audience_type is BroadcastAudienceType.MANUAL:
            raw_ids = parameters.get("user_ids", [])
            user_ids = [int(value) for value in raw_ids] if isinstance(raw_ids, list) else []
            filters.append(User.id.in_(user_ids))
        result = await self._session.scalars(
            select(User.id)
            .join(BusinessClient, BusinessClient.user_id == User.id)
            .where(*filters)
            .order_by(User.id)
        )
        return list(result.all())

    async def get_recipient(
        self, recipient_id: int, *, for_update: bool = False
    ) -> BroadcastRecipient | None:
        statement = select(BroadcastRecipient).where(
            BroadcastRecipient.id == recipient_id,
            BroadcastRecipient.business_id == self.business_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def get_recipient_for_user(
        self, broadcast_id: int, user_id: int
    ) -> BroadcastRecipient | None:
        return (
            await self._session.scalars(
                select(BroadcastRecipient).where(
                    BroadcastRecipient.business_id == self.business_id,
                    BroadcastRecipient.broadcast_id == broadcast_id,
                    BroadcastRecipient.user_id == user_id,
                )
            )
        ).one_or_none()

    async def claim_due_recipients(
        self,
        *,
        now: datetime,
        worker_id: str,
        limit: int,
        lease_expired_before: datetime,
    ) -> list[BroadcastRecipient]:
        result = await self._session.scalars(
            select(BroadcastRecipient)
            .join(Broadcast, Broadcast.id == BroadcastRecipient.broadcast_id)
            .where(
                Broadcast.business_id == self.business_id,
                BroadcastRecipient.business_id == self.business_id,
                Broadcast.status.in_(
                    (
                        BroadcastStatus.SCHEDULED,
                        BroadcastStatus.PREPARING,
                        BroadcastStatus.SENDING,
                    )
                ),
                Broadcast.scheduled_at <= now,
                or_(
                    and_(
                        BroadcastRecipient.status.in_(
                            (
                                BroadcastRecipientStatus.PENDING,
                                BroadcastRecipientStatus.RETRY,
                            )
                        ),
                        BroadcastRecipient.available_at <= now,
                    ),
                    and_(
                        BroadcastRecipient.status == BroadcastRecipientStatus.PROCESSING,
                        BroadcastRecipient.locked_at <= lease_expired_before,
                    ),
                ),
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

    async def cancel_open_recipients(self, broadcast_id: int) -> int:
        recipients = list(
            (
                await self._session.scalars(
                    select(BroadcastRecipient)
                    .where(
                        BroadcastRecipient.business_id == self.business_id,
                        BroadcastRecipient.broadcast_id == broadcast_id,
                        BroadcastRecipient.status.in_(
                            (
                                BroadcastRecipientStatus.PENDING,
                                BroadcastRecipientStatus.RETRY,
                                BroadcastRecipientStatus.PROCESSING,
                            )
                        ),
                    )
                    .with_for_update()
                )
            ).all()
        )
        for recipient in recipients:
            recipient.status = BroadcastRecipientStatus.SKIPPED
            recipient.locked_at = None
            recipient.locked_by = None
            recipient.last_error = "broadcast_cancelled"
        await self._session.flush()
        return len(recipients)

    async def status_counts(self, broadcast_id: int) -> dict[BroadcastRecipientStatus, int]:
        rows = await self._session.execute(
            select(BroadcastRecipient.status, func.count(BroadcastRecipient.id))
            .where(
                BroadcastRecipient.business_id == self.business_id,
                BroadcastRecipient.broadcast_id == broadcast_id,
            )
            .group_by(BroadcastRecipient.status)
        )
        return {status: int(count) for status, count in rows.all()}

    async def add_event(self, event: MarketingEvent) -> MarketingEvent:
        self._require_business(event.business_id)
        self._session.add(event)
        await self._session.flush()
        return event

    @staticmethod
    def _int_parameter(value: object, *, default: int) -> int:
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            try:
                return int(value)
            except ValueError:
                return default
        return default
