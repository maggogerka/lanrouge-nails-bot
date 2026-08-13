"""White-label business setup is live-authorized and audit-safe."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.database.models.business import Business
from app.domain.enums import BusinessStatus, BusinessType, StaffRole
from app.domain.errors import AuthorizationError, BusinessTypeTransitionError
from app.schemas.authorization import StaffContext, StaffPermission
from app.schemas.business import BusinessProfileUpdate
from app.services.business_service import BusinessAdministrationService

NOW = datetime(2026, 8, 11, 12, tzinfo=UTC)


def actor() -> StaffContext:
    return StaffContext(
        business_id=1,
        staff_member_id=2,
        user_id=3,
        telegram_id=4,
        display_name="Owner",
        role=StaffRole.OWNER,
        is_bookable=True,
    )


def business() -> Business:
    return Business(
        id=1,
        slug="example-business",
        display_name="Old brand",
        description=None,
        short_description=None,
        business_type=BusinessType.SOLO,
        status=BusinessStatus.SETUP,
        timezone="Europe/Moscow",
        currency="RUB",
        address=None,
        social_links={},
        privacy_policy_url=None,
        instance_id="example-instance",
    )


@pytest.mark.asyncio
async def test_owner_completes_business_setup_without_auditing_profile_values() -> None:
    live_actor = actor()
    authorization = MagicMock()
    authorization.authorize = AsyncMock(return_value=live_actor)
    unit_of_work = MagicMock()
    unit_of_work.business_id = 1
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    unit_of_work.businesses.get = AsyncMock(return_value=business())
    unit_of_work.businesses.flush = AsyncMock()
    unit_of_work.settings.get = AsyncMock(return_value=None)
    unit_of_work.audit.add = AsyncMock()
    unit_of_work.commit = AsyncMock()
    service = BusinessAdministrationService(
        lambda: unit_of_work,  # type: ignore[arg-type]
        authorization,
    )

    view = await service.update(
        live_actor,
        BusinessProfileUpdate(
            display_name="New brand",
            address="Москва",
            privacy_policy_url="https://example.test/privacy",
        ),
        correlation_id="request-12345678",
        now=NOW,
    )

    assert view.display_name == "New brand"
    assert view.status is BusinessStatus.ACTIVE
    assert view.setup_completed_at == NOW
    authorization.authorize.assert_awaited_once_with(
        business_id=1,
        telegram_id=4,
        permission=StaffPermission.MANAGE_BUSINESS,
    )
    changes = unit_of_work.audit.add.await_args.kwargs["changes"]
    assert changes == {"changed_fields": ["display_name", "address", "privacy_policy_url"]}
    assert "New brand" not in str(changes)


def test_business_update_rejects_unsafe_urls_and_null_required_fields() -> None:
    with pytest.raises(ValidationError):
        BusinessProfileUpdate(privacy_policy_url="http://example.test/privacy")
    with pytest.raises(ValidationError):
        BusinessProfileUpdate(display_name=None)


@pytest.mark.asyncio
async def test_salon_to_solo_reports_every_repository_blocker() -> None:
    live_actor = actor()
    authorization = MagicMock()
    authorization.authorize = AsyncMock(return_value=live_actor)
    current = business()
    current.business_type = BusinessType.SALON
    unit_of_work = MagicMock()
    unit_of_work.business_id = 1
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    unit_of_work.businesses.get = AsyncMock(return_value=current)
    unit_of_work.staff.get_bootstrap_owner = AsyncMock(return_value=SimpleNamespace(id=2))
    unit_of_work.staff.solo_transition_blockers = AsyncMock(
        return_value=(
            "активные сотрудники: Мастер",
            "будущие записи других специалистов: 2",
        )
    )
    unit_of_work.commit = AsyncMock()
    service = BusinessAdministrationService(
        lambda: unit_of_work,  # type: ignore[arg-type]
        authorization,
    )

    with pytest.raises(BusinessTypeTransitionError) as exc_info:
        await service.update(
            live_actor,
            BusinessProfileUpdate(business_type=BusinessType.SOLO),
            now=NOW,
        )

    assert exc_info.value.blockers == (
        "активные сотрудники: Мастер",
        "будущие записи других специалистов: 2",
    )
    unit_of_work.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_only_bootstrap_can_enable_its_solo_specialist_profile() -> None:
    live_actor = actor().model_copy(update={"is_bootstrap_owner": True, "is_bookable": False})
    authorization = MagicMock()
    authorization.authorize = AsyncMock(return_value=live_actor)
    current = business()
    member_row = SimpleNamespace(id=2, is_bootstrap_owner=True, is_bookable=False)
    unit_of_work = MagicMock()
    unit_of_work.business_id = 1
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    unit_of_work.businesses.get = AsyncMock(return_value=current)
    unit_of_work.staff.get_by_id = AsyncMock(return_value=member_row)
    unit_of_work.staff.flush = AsyncMock()
    unit_of_work.audit.add = AsyncMock()
    unit_of_work.commit = AsyncMock()
    service = BusinessAdministrationService(
        lambda: unit_of_work,  # type: ignore[arg-type]
        authorization,
    )

    updated = await service.set_bootstrap_bookable(live_actor, enabled=True)

    assert updated.is_bookable
    assert member_row.is_bookable
    unit_of_work.commit.assert_awaited_once()

    authorization.authorize = AsyncMock(return_value=actor())
    with pytest.raises(AuthorizationError, match="bootstrap"):
        await service.set_bootstrap_bookable(actor(), enabled=True)


@pytest.mark.asyncio
async def test_welcome_draft_does_not_replace_public_content_until_publish() -> None:
    live_actor = actor()
    authorization = MagicMock()
    authorization.authorize = AsyncMock(return_value=live_actor)
    current = business()
    current.welcome_published_text = "Старое приветствие"
    current.welcome_published_photo_file_id = "old-photo"
    unit_of_work = MagicMock()
    unit_of_work.business_id = 1
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    unit_of_work.businesses.get = AsyncMock(return_value=current)
    unit_of_work.businesses.flush = AsyncMock()
    unit_of_work.audit.add = AsyncMock()
    unit_of_work.commit = AsyncMock()
    service = BusinessAdministrationService(
        lambda: unit_of_work,  # type: ignore[arg-type]
        authorization,
    )

    draft = await service.save_welcome_text(
        live_actor,
        "<b>Новое</b> & безопасное",
        correlation_id="welcome-draft",
    )

    assert draft.draft_text == "<b>Новое</b> &amp; безопасное"
    assert draft.published_text == "Старое приветствие"
    assert current.welcome_published_photo_file_id == "old-photo"

    current.welcome_draft_photo_file_id = "new-photo"
    current.welcome_draft_photo_unique_id = "new-photo-unique"
    published = await service.publish_welcome(live_actor, correlation_id="welcome-publish", now=NOW)
    assert published.published_text == draft.draft_text
    assert published.published_photo_file_id == "new-photo"
    assert published.published_at == NOW


@pytest.mark.asyncio
async def test_business_location_is_synchronized_with_legacy_runtime_settings() -> None:
    live_actor = actor()
    authorization = MagicMock()
    authorization.authorize = AsyncMock(return_value=live_actor)
    current = business()
    current.social_links = {}
    legacy = SimpleNamespace(
        business_name="Old",
        timezone="Europe/Moscow",
        address="Old address",
        map_url="https://example.test/old",
        version=3,
    )
    unit_of_work = MagicMock()
    unit_of_work.business_id = 1
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    unit_of_work.businesses.get = AsyncMock(return_value=current)
    unit_of_work.businesses.flush = AsyncMock()
    unit_of_work.settings.get = AsyncMock(return_value=legacy)
    unit_of_work.audit.add = AsyncMock()
    unit_of_work.commit = AsyncMock()
    service = BusinessAdministrationService(
        lambda: unit_of_work,  # type: ignore[arg-type]
        authorization,
    )

    view = await service.update(
        live_actor,
        BusinessProfileUpdate(
            address="Новый адрес",
            map_url="https://maps.example.test/new",
        ),
        now=NOW,
    )

    assert view.address == "Новый адрес"
    assert view.map_url == "https://maps.example.test/new"
    assert legacy.address == "Новый адрес"
    assert legacy.map_url == "https://maps.example.test/new"
    assert legacy.version == 4
