"""Independent privacy and marketing consent transaction tests."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.models import User
from app.domain.errors import PrivacyConsentRequiredError
from app.schemas.booking import ClientActor
from app.services.consent_service import ConsentService

NOW = datetime(2026, 7, 22, 9, tzinfo=UTC)


def actor() -> ClientActor:
    return ClientActor(telegram_id=101, username="client", first_name="Client")


def user() -> User:
    return User(id=5, telegram_id=101, first_name="Client")


def build_uow(client: User) -> MagicMock:
    unit_of_work = MagicMock()
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    unit_of_work.users.get_or_create_client = AsyncMock(return_value=client)

    async def set_privacy(target: User, changed_at: datetime) -> None:
        target.privacy_consent_at = changed_at

    async def set_marketing(
        target: User,
        *,
        accepted: bool,
        changed_at: datetime,
    ) -> None:
        target.marketing_consent_at = changed_at if accepted else None
        target.marketing_unsubscribed_at = None if accepted else changed_at

    unit_of_work.users.set_privacy_consent = AsyncMock(side_effect=set_privacy)
    unit_of_work.users.set_marketing_consent = AsyncMock(side_effect=set_marketing)
    unit_of_work.audit.add = AsyncMock()
    unit_of_work.commit = AsyncMock()
    return unit_of_work


@pytest.mark.asyncio
async def test_privacy_and_marketing_are_separate_decisions() -> None:
    client = user()
    unit_of_work = build_uow(client)
    service = ConsentService(lambda: unit_of_work)  # type: ignore[arg-type]

    privacy = await service.accept_privacy(actor(), now=NOW)
    marketing = await service.set_marketing(actor(), accepted=False, now=NOW)

    assert privacy.privacy_accepted
    assert not privacy.marketing_answered
    assert marketing.marketing_answered
    assert not marketing.marketing_accepted
    assert [call.kwargs["action"] for call in unit_of_work.audit.add.await_args_list] == [
        "consent.privacy_accepted",
        "consent.marketing_changed",
    ]
    assert unit_of_work.commit.await_count == 2


@pytest.mark.asyncio
async def test_marketing_cannot_be_answered_before_privacy() -> None:
    unit_of_work = build_uow(user())
    service = ConsentService(lambda: unit_of_work)  # type: ignore[arg-type]

    with pytest.raises(PrivacyConsentRequiredError):
        await service.set_marketing(actor(), accepted=True, now=NOW)

    unit_of_work.users.set_marketing_consent.assert_not_awaited()
    unit_of_work.commit.assert_not_awaited()
