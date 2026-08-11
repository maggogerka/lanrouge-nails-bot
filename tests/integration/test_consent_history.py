"""PostgreSQL persistence for independent append-only consent preferences."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.database import Database
from app.database.models import ConsentHistory, User
from app.domain.enums import ConsentSource, ConsentType, UserRole
from app.repositories import SqlAlchemyUnitOfWork
from app.schemas.booking import ClientActor
from app.services.consent_service import ConsentService

NOW = datetime(2026, 7, 22, 9, tzinfo=UTC)


@pytest.mark.asyncio
async def test_preference_changes_are_append_only_and_service_messages_stay_on(
    integration_database: Database,
) -> None:
    async with integration_database.sessions() as session:
        session.add(
            User(
                telegram_id=101,
                role=UserRole.CLIENT,
                privacy_consent_at=NOW,
                marketing_unsubscribed_at=NOW,
            )
        )
        await session.commit()

    service = ConsentService(
        lambda: SqlAlchemyUnitOfWork(integration_database.sessions),
        fallback_privacy_policy_url="https://example.test/privacy",
    )
    actor = ClientActor(telegram_id=101, username="client", first_name="Client")
    await service.accept_privacy(actor, now=NOW)
    await service.set_marketing(
        actor,
        accepted=True,
        source=ConsentSource.NOTIFICATION_SETTINGS,
        now=NOW,
    )
    preferences = await service.set_repeat_booking(actor, accepted=False, now=NOW)

    assert preferences.service_notifications_enabled
    assert preferences.marketing_enabled
    assert not preferences.repeat_booking_enabled
    async with integration_database.sessions() as session:
        histories = list(
            (await session.scalars(select(ConsentHistory).order_by(ConsentHistory.id))).all()
        )
    assert [item.consent_type for item in histories] == [
        ConsentType.PRIVACY,
        ConsentType.MARKETING,
        ConsentType.REPEAT_BOOKING,
    ]
    assert histories[0].source is ConsentSource.ONBOARDING
    assert all(item.source is ConsentSource.NOTIFICATION_SETTINGS for item in histories[1:])
