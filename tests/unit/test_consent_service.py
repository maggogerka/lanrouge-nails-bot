"""Independent privacy and marketing consent transaction tests."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.models import Business, BusinessClient, ConsentHistory, User
from app.domain.enums import ConsentSource, ConsentType
from app.domain.errors import PrivacyConsentRequiredError
from app.domain.legal import marketing_consent_policy
from app.domain.privacy import PolicyDocument
from app.schemas.booking import ClientActor
from app.services.consent_service import ConsentService

NOW = datetime(2026, 7, 22, 9, tzinfo=UTC)
PRIVACY_POLICY = PolicyDocument(
    version="2026-08",
    url="https://example.com/privacy",
    sha256=None,
)


def actor() -> ClientActor:
    return ClientActor(telegram_id=101, username="client", first_name="Client")


def user() -> User:
    return User(id=5, telegram_id=101, first_name="Client")


def build_uow(client: User) -> MagicMock:
    unit_of_work = MagicMock()
    unit_of_work.business_id = 1
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
    unit_of_work.crm.add_consent_history = AsyncMock()
    unit_of_work.audit.add = AsyncMock()
    business_client = BusinessClient(id=15, business_id=1, user_id=client.id, is_active=True)
    unit_of_work.privacy.business_id = 1
    unit_of_work.privacy.get_business = AsyncMock(
        return_value=Business(
            id=1,
            slug="test-business",
            display_name="Test business",
            instance_id="test-instance",
            timezone="Europe/Moscow",
            currency="RUB",
            privacy_policy_url=PRIVACY_POLICY.url,
            privacy_policy_version=PRIVACY_POLICY.version,
        )
    )
    unit_of_work.privacy.get_client_by_user = AsyncMock(return_value=business_client)
    unit_of_work.privacy.get_client = AsyncMock(return_value=business_client)
    unit_of_work.privacy.get_open_deletion_request = AsyncMock(return_value=None)

    async def add_deletion_request(request: object) -> object:
        request.id = 21  # type: ignore[attr-defined]
        return request

    unit_of_work.privacy.add_deletion_request = AsyncMock(side_effect=add_deletion_request)
    unit_of_work.privacy.add_deletion_event = AsyncMock()
    consent_entries: list[ConsentHistory] = []

    async def latest_consent(user_id: int, consent_type: ConsentType) -> ConsentHistory | None:
        matching = [
            item
            for item in consent_entries
            if item.user_id == user_id and item.consent_type is consent_type
        ]
        return matching[-1] if matching else None

    async def add_consent(entry: ConsentHistory) -> ConsentHistory:
        entry.id = len(consent_entries) + 1
        consent_entries.append(entry)
        return entry

    unit_of_work.consent_entries = consent_entries
    unit_of_work.privacy.latest_consent = AsyncMock(side_effect=latest_consent)
    unit_of_work.privacy.add_consent = AsyncMock(side_effect=add_consent)
    unit_of_work.privacy.flush = AsyncMock()
    unit_of_work.commit = AsyncMock()
    return unit_of_work


def seed_consent(
    unit_of_work: MagicMock,
    consent_type: ConsentType,
    *,
    accepted: bool,
    policy: PolicyDocument,
) -> None:
    unit_of_work.consent_entries.append(
        ConsentHistory(
            id=len(unit_of_work.consent_entries) + 1,
            business_id=1,
            user_id=5,
            consent_type=consent_type,
            previous_value=None,
            new_value=accepted,
            source=ConsentSource.ONBOARDING,
            policy_version=policy.version,
            policy_url=policy.url,
            policy_hash=policy.sha256,
            revoked_at=None if accepted else NOW,
            created_at=NOW,
        )
    )


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
    histories = unit_of_work.consent_entries
    assert [item.consent_type for item in histories] == [
        ConsentType.PRIVACY,
        ConsentType.MARKETING,
    ]


@pytest.mark.asyncio
async def test_marketing_cannot_be_answered_before_privacy() -> None:
    unit_of_work = build_uow(user())
    service = ConsentService(lambda: unit_of_work)  # type: ignore[arg-type]

    with pytest.raises(PrivacyConsentRequiredError):
        await service.set_marketing(actor(), accepted=True, now=NOW)

    unit_of_work.users.set_marketing_consent.assert_not_awaited()
    unit_of_work.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_legacy_unversioned_privacy_consent_requires_explicit_reconsent() -> None:
    client = user()
    client.privacy_consent_at = NOW
    unit_of_work = build_uow(client)
    unit_of_work.consent_entries.append(
        ConsentHistory(
            id=1,
            business_id=1,
            user_id=client.id,
            consent_type=ConsentType.PRIVACY,
            previous_value=None,
            new_value=True,
            source=ConsentSource.ONBOARDING,
            policy_version="legacy-unversioned",
            policy_url=None,
            policy_hash=None,
            created_at=NOW,
        )
    )
    service = ConsentService(lambda: unit_of_work)  # type: ignore[arg-type]

    status = await service.get_or_create_status(actor())

    assert not status.privacy_accepted


@pytest.mark.asyncio
async def test_privacy_policy_version_change_requires_reconsent() -> None:
    client = user()
    unit_of_work = build_uow(client)
    service = ConsentService(lambda: unit_of_work)  # type: ignore[arg-type]
    assert (await service.accept_privacy(actor(), now=NOW)).privacy_accepted

    unit_of_work.privacy.get_business.return_value.privacy_policy_version = "2026-09"

    assert not (await service.get_or_create_status(actor())).privacy_accepted


@pytest.mark.asyncio
async def test_deletion_request_disables_marketing_and_is_audited() -> None:
    client = user()
    client.privacy_consent_at = NOW
    client.marketing_consent_at = NOW
    unit_of_work = build_uow(client)
    seed_consent(
        unit_of_work,
        ConsentType.PRIVACY,
        accepted=True,
        policy=PRIVACY_POLICY,
    )
    service = ConsentService(lambda: unit_of_work)  # type: ignore[arg-type]

    await service.request_deletion(actor(), now=NOW, correlation_id="request-delete")

    unit_of_work.users.set_marketing_consent.assert_awaited_once_with(
        client,
        accepted=False,
        changed_at=NOW,
    )
    audit_actions = [call.kwargs["action"] for call in unit_of_work.audit.add.await_args_list]
    assert audit_actions == [
        "privacy.deletion_requested",
        "privacy.deletion_request_persisted",
    ]
    assert all(
        call.kwargs["correlation_id"] == "request-delete"
        for call in unit_of_work.audit.add.await_args_list
    )
    unit_of_work.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_notification_settings_are_independent_and_audited() -> None:
    client = user()
    client.privacy_consent_at = NOW
    client.marketing_unsubscribed_at = NOW
    unit_of_work = build_uow(client)
    seed_consent(
        unit_of_work,
        ConsentType.PRIVACY,
        accepted=True,
        policy=PRIVACY_POLICY,
    )
    service = ConsentService(lambda: unit_of_work)  # type: ignore[arg-type]

    marketing = await service.set_marketing(
        actor(),
        accepted=True,
        source=ConsentSource.NOTIFICATION_SETTINGS,
        now=NOW,
    )
    repeat = await service.set_repeat_booking(actor(), accepted=False, now=NOW)

    assert marketing.marketing_accepted
    assert not repeat.repeat_booking_enabled
    assert repeat.service_notifications_enabled
    marketing_history = unit_of_work.consent_entries[-1]
    repeat_history = unit_of_work.crm.add_consent_history.await_args.args[0]
    assert (
        marketing_history.consent_type,
        marketing_history.previous_value,
        marketing_history.new_value,
    ) == (ConsentType.MARKETING, None, True)
    assert (
        repeat_history.consent_type,
        repeat_history.previous_value,
        repeat_history.new_value,
    ) == (ConsentType.REPEAT_BOOKING, True, False)
    assert marketing_history.source is ConsentSource.NOTIFICATION_SETTINGS
    assert repeat_history.source is ConsentSource.NOTIFICATION_SETTINGS


@pytest.mark.asyncio
async def test_repeating_same_preference_does_not_duplicate_history() -> None:
    client = user()
    client.privacy_consent_at = NOW
    client.marketing_consent_at = NOW
    unit_of_work = build_uow(client)
    seed_consent(
        unit_of_work,
        ConsentType.PRIVACY,
        accepted=True,
        policy=PRIVACY_POLICY,
    )
    seed_consent(
        unit_of_work,
        ConsentType.MARKETING,
        accepted=True,
        policy=marketing_consent_policy(),
    )
    service = ConsentService(lambda: unit_of_work)  # type: ignore[arg-type]

    await service.set_marketing(actor(), accepted=True, now=NOW)
    await service.set_repeat_booking(actor(), accepted=True, now=NOW)

    unit_of_work.crm.add_consent_history.assert_not_awaited()
    assert len(unit_of_work.consent_entries) == 2
