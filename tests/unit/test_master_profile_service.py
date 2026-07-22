"""Master profile publication, validation, links and authorization tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.database.models import MasterProfile, MasterPublicLink
from app.domain.errors import AuthorizationError, EntityNotFoundError
from app.schemas.master_profile import MasterProfileUpdate, MasterPublicLinkInput
from app.schemas.service import AdminActor
from app.services.master_profile_service import MasterProfileService


def admin() -> AdminActor:
    return AdminActor(telegram_id=900, first_name="Master")


def build_uow(*, published: bool = False, enabled: bool = True) -> MagicMock:
    unit_of_work = MagicMock()
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    unit_of_work.users.get_or_create_admin = AsyncMock(return_value=SimpleNamespace(id=9))
    unit_of_work.settings.get = AsyncMock(
        return_value=SimpleNamespace(master_profile_enabled=enabled)
    )
    profile = MasterProfile(
        id=1,
        display_name="Lanrouge nails",
        bio=None,
        address=None,
        map_url=None,
        telegram_url=None,
        is_published=published,
    )
    unit_of_work.master_profile.get = AsyncMock(return_value=profile)
    unit_of_work.master_profile.list_links = AsyncMock(return_value=[])
    unit_of_work.audit.add = AsyncMock()
    unit_of_work.commit = AsyncMock()
    return unit_of_work


@pytest.mark.asyncio
async def test_non_admin_cannot_read_admin_profile() -> None:
    factory = MagicMock()
    service = MasterProfileService(factory, frozenset({900}))

    with pytest.raises(AuthorizationError):
        await service.get_admin(AdminActor(telegram_id=901))

    factory.assert_not_called()


@pytest.mark.asyncio
async def test_unpublished_profile_is_hidden_from_client() -> None:
    unit_of_work = build_uow(published=False)
    service = MasterProfileService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    assert await service.get_public() is None


@pytest.mark.asyncio
async def test_disabled_profile_is_hidden_even_when_published() -> None:
    unit_of_work = build_uow(published=True, enabled=False)
    service = MasterProfileService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    assert await service.get_public() is None


@pytest.mark.asyncio
async def test_public_profile_contains_only_active_sorted_links() -> None:
    unit_of_work = build_uow(published=True)
    unit_of_work.master_profile.list_links = AsyncMock(
        return_value=[
            MasterPublicLink(
                id=2,
                profile_id=1,
                label="Site",
                url="https://example.com",
                sort_order=10,
                is_active=True,
                created_by_user_id=9,
                updated_by_user_id=9,
            )
        ]
    )
    service = MasterProfileService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    result = await service.get_public()

    assert result is not None
    assert [link.label for link in result.links] == ["Site"]
    unit_of_work.master_profile.list_links.assert_awaited_once_with(active_only=True)


@pytest.mark.asyncio
async def test_admin_profile_includes_inactive_links() -> None:
    unit_of_work = build_uow()
    service = MasterProfileService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    await service.get_admin(admin())

    unit_of_work.master_profile.list_links.assert_awaited_once_with(active_only=False)


@pytest.mark.asyncio
async def test_update_audit_contains_field_names_not_sensitive_content() -> None:
    unit_of_work = build_uow()
    service = MasterProfileService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    result = await service.update(
        admin(), MasterProfileUpdate(bio="Private draft", map_url="https://maps.example/place")
    )

    assert result.bio == "Private draft"
    changes = unit_of_work.audit.add.await_args.kwargs["changes"]
    assert set(changes["changed_fields"]) == {"bio", "map_url"}
    assert "Private draft" not in str(changes)
    assert "https://" not in str(changes)
    unit_of_work.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_publication_is_explicit_and_audited() -> None:
    unit_of_work = build_uow()
    service = MasterProfileService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    result = await service.set_published(admin(), True, correlation_id="publish-profile")

    assert result.is_published
    assert unit_of_work.audit.add.await_args.kwargs["action"] == "master_profile.published"
    assert unit_of_work.audit.add.await_args.kwargs["correlation_id"] == "publish-profile"


@pytest.mark.asyncio
async def test_missing_singleton_profile_fails_closed() -> None:
    unit_of_work = build_uow()
    unit_of_work.master_profile.get = AsyncMock(return_value=None)
    service = MasterProfileService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="row is missing"):
        await service.get_public()


@pytest.mark.asyncio
async def test_add_link_sets_actor_and_audits_without_url() -> None:
    unit_of_work = build_uow()

    async def add_link(link: MasterPublicLink) -> MasterPublicLink:
        link.id = 14
        return link

    unit_of_work.master_profile.add_link = AsyncMock(side_effect=add_link)
    service = MasterProfileService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    result = await service.add_link(
        admin(), MasterPublicLinkInput(label="VK", url="https://vk.com/example", sort_order=20)
    )

    assert result.id == 14
    saved = unit_of_work.master_profile.add_link.await_args.args[0]
    assert saved.created_by_user_id == 9
    assert saved.updated_by_user_id == 9
    assert "https://" not in str(unit_of_work.audit.add.await_args.kwargs["changes"])


@pytest.mark.asyncio
async def test_update_missing_link_is_rejected() -> None:
    unit_of_work = build_uow()
    unit_of_work.master_profile.get_link = AsyncMock(return_value=None)
    service = MasterProfileService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    with pytest.raises(EntityNotFoundError):
        await service.update_link(
            admin(), 44, MasterPublicLinkInput(label="Site", url="https://example.com")
        )

    unit_of_work.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_link_is_audited_and_committed() -> None:
    unit_of_work = build_uow()
    link = MasterPublicLink(
        id=7,
        profile_id=1,
        label="Site",
        url="https://example.com",
        sort_order=0,
        is_active=True,
        created_by_user_id=9,
        updated_by_user_id=9,
    )
    unit_of_work.master_profile.get_link = AsyncMock(return_value=link)
    unit_of_work.master_profile.delete_link = AsyncMock()
    service = MasterProfileService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    await service.delete_link(admin(), 7)

    unit_of_work.master_profile.delete_link.assert_awaited_once_with(link)
    unit_of_work.commit.assert_awaited_once()


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "javascript:alert(1)",
        "https:///missing-host",
        "https://user:password@example.com",
    ],
)
def test_link_rejects_unsafe_or_non_absolute_url(url: str) -> None:
    with pytest.raises(ValidationError, match="HTTPS URL"):
        MasterPublicLinkInput(label="Unsafe", url=url)


def test_profile_update_rejects_partial_photo_identifiers() -> None:
    with pytest.raises(ValidationError, match="both Telegram photo"):
        MasterProfileUpdate(telegram_photo_file_id="file-id")


def test_profile_update_allows_clearing_both_photo_identifiers() -> None:
    values = MasterProfileUpdate(
        telegram_photo_file_id=None,
        telegram_photo_file_unique_id=None,
    )

    assert values.model_dump(exclude_unset=True) == {
        "telegram_photo_file_id": None,
        "telegram_photo_file_unique_id": None,
    }
