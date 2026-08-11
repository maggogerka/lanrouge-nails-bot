"""PostgreSQL consent filtering and immutable broadcast audience snapshots."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.database import Database
from app.database.models import Broadcast, BroadcastRecipient, BusinessClient, User
from app.domain.enums import (
    BroadcastAudienceType,
    BroadcastButtonType,
    BroadcastStatus,
    UserRole,
)
from app.repositories import SqlAlchemyUnitOfWork

NOW = datetime(2026, 7, 22, 9, tzinfo=UTC)


@pytest.mark.asyncio
async def test_only_live_subscribers_are_frozen_once(
    integration_database: Database,
) -> None:
    async with integration_database.sessions() as session:
        admin = User(telegram_id=900, role=UserRole.ADMIN)
        subscribed = User(
            telegram_id=101,
            role=UserRole.CLIENT,
            marketing_consent_at=NOW,
            is_blocked=False,
        )
        unsubscribed = User(
            telegram_id=102,
            role=UserRole.CLIENT,
            marketing_consent_at=None,
            marketing_unsubscribed_at=NOW,
            is_blocked=False,
        )
        blocked = User(
            telegram_id=103,
            role=UserRole.CLIENT,
            marketing_consent_at=NOW,
            is_blocked=True,
        )
        self_booking_blocked = User(
            telegram_id=104,
            role=UserRole.CLIENT,
            marketing_consent_at=NOW,
            is_blocked=False,
            is_self_booking_blocked=True,
        )
        no_consent = User(
            telegram_id=105,
            role=UserRole.CLIENT,
            is_blocked=False,
        )
        session.add_all(
            [admin, subscribed, unsubscribed, blocked, self_booking_blocked, no_consent]
        )
        await session.flush()
        session.add_all(
            BusinessClient(business_id=1, user_id=user.id)
            for user in (subscribed, unsubscribed, blocked, self_booking_blocked, no_consent)
        )
        campaign = Broadcast(
            business_id=1,
            title="Campaign",
            text="Text",
            status=BroadcastStatus.SCHEDULED,
            audience_type=BroadcastAudienceType.ALL_SUBSCRIBED,
            audience_parameters={},
            button_type=BroadcastButtonType.NONE,
            scheduled_at=NOW,
            created_by=admin.id,
        )
        session.add(campaign)
        await session.commit()

    async with SqlAlchemyUnitOfWork(integration_database.sessions) as uow:
        user_ids = await uow.broadcasts.resolve_audience_user_ids(
            audience_type=BroadcastAudienceType.ALL_SUBSCRIBED,
            parameters={},
            now=NOW,
        )
        assert user_ids == [2]
        assert (
            await uow.broadcasts.freeze_recipients(
                broadcast_id=1, user_ids=user_ids, scheduled_at=NOW
            )
            == 1
        )
        assert (
            await uow.broadcasts.freeze_recipients(
                broadcast_id=1, user_ids=user_ids, scheduled_at=NOW
            )
            == 0
        )
        await uow.commit()

    async with integration_database.sessions() as session:
        late_subscriber = User(
            telegram_id=106,
            role=UserRole.CLIENT,
            marketing_consent_at=NOW,
            is_blocked=False,
        )
        session.add(late_subscriber)
        await session.flush()
        session.add(BusinessClient(business_id=1, user_id=late_subscriber.id))
        await session.commit()
        count = await session.scalar(select(func.count(BroadcastRecipient.id)))
        assert count == 1
