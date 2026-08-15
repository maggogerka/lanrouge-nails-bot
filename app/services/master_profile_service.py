"""Administration and public projection of the master's profile."""

from __future__ import annotations

from collections.abc import Callable

from app.database.models import MasterProfile, MasterPublicLink
from app.domain.errors import EntityNotFoundError
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.master_profile import (
    MasterProfileUpdate,
    MasterProfileView,
    MasterPublicLinkInput,
    MasterPublicLinkView,
)
from app.schemas.service import AdminActor
from app.services.appointment_common import ensure_admin

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


class MasterProfileService:
    """Keep public master content unpublished until an admin explicitly enables it."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        admin_telegram_ids: frozenset[int],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._admin_telegram_ids = admin_telegram_ids

    async def get_admin(self, actor: AdminActor) -> MasterProfileView:
        ensure_admin(actor, self._admin_telegram_ids)
        async with self._unit_of_work_factory() as unit_of_work:
            return await self._view(
                unit_of_work, await self._profile(unit_of_work), active_only=False
            )

    async def get_public(self) -> MasterProfileView | None:
        async with self._unit_of_work_factory() as unit_of_work:
            settings = await unit_of_work.settings.get()
            profile = await self._profile(unit_of_work)
            if settings is None:
                raise RuntimeError("Business settings row is missing")
            if not settings.master_profile_enabled or not profile.is_published:
                return None
            return await self._view(unit_of_work, profile, active_only=True)

    async def update(
        self,
        actor: AdminActor,
        values: MasterProfileUpdate,
        *,
        correlation_id: str | None = None,
    ) -> MasterProfileView:
        ensure_admin(actor, self._admin_telegram_ids)
        async with self._unit_of_work_factory() as unit_of_work:
            admin = await unit_of_work.users.get_or_create_admin(actor)
            profile = await self._profile(unit_of_work, for_update=True)
            changes = values.model_dump(exclude_unset=True)
            changed_fields: list[str] = []
            for field, value in changes.items():
                if getattr(profile, field) != value:
                    setattr(profile, field, value)
                    changed_fields.append(field)
            profile.updated_by_user_id = admin.id
            await unit_of_work.audit.add(
                actor_user_id=admin.id,
                action="master_profile.updated",
                entity_type="master_profile",
                entity_id="1",
                changes={"changed_fields": changed_fields},
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return await self._view(unit_of_work, profile, active_only=False)

    async def set_published(
        self,
        actor: AdminActor,
        published: bool,
        *,
        correlation_id: str | None = None,
    ) -> MasterProfileView:
        ensure_admin(actor, self._admin_telegram_ids)
        async with self._unit_of_work_factory() as unit_of_work:
            admin = await unit_of_work.users.get_or_create_admin(actor)
            profile = await self._profile(unit_of_work, for_update=True)
            before = profile.is_published
            profile.is_published = published
            profile.updated_by_user_id = admin.id
            await unit_of_work.audit.add(
                actor_user_id=admin.id,
                action="master_profile.published" if published else "master_profile.unpublished",
                entity_type="master_profile",
                entity_id="1",
                changes={"is_published": {"before": before, "after": published}},
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return await self._view(unit_of_work, profile, active_only=False)

    async def add_link(
        self,
        actor: AdminActor,
        values: MasterPublicLinkInput,
        *,
        correlation_id: str | None = None,
    ) -> MasterPublicLinkView:
        ensure_admin(actor, self._admin_telegram_ids)
        async with self._unit_of_work_factory() as unit_of_work:
            admin = await unit_of_work.users.get_or_create_admin(actor)
            profile = await self._profile(unit_of_work)
            link = await unit_of_work.master_profile.add_link(
                MasterPublicLink(
                    business_id=unit_of_work.business_id,
                    profile_id=profile.id,
                    created_by_user_id=admin.id,
                    updated_by_user_id=admin.id,
                    **values.model_dump(),
                )
            )
            await unit_of_work.audit.add(
                actor_user_id=admin.id,
                action="master_public_link.created",
                entity_type="master_public_link",
                entity_id=str(link.id),
                changes={
                    "label": link.label,
                    "sort_order": link.sort_order,
                    "is_active": link.is_active,
                },
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return MasterPublicLinkView.model_validate(link)

    async def update_link(
        self,
        actor: AdminActor,
        link_id: int,
        values: MasterPublicLinkInput,
        *,
        correlation_id: str | None = None,
    ) -> MasterPublicLinkView:
        ensure_admin(actor, self._admin_telegram_ids)
        async with self._unit_of_work_factory() as unit_of_work:
            admin = await unit_of_work.users.get_or_create_admin(actor)
            link = await unit_of_work.master_profile.get_link(link_id, for_update=True)
            if link is None:
                raise EntityNotFoundError("Ссылка больше не существует.")
            changed_fields = [
                field
                for field, value in values.model_dump().items()
                if getattr(link, field) != value
            ]
            for field, value in values.model_dump().items():
                setattr(link, field, value)
            link.updated_by_user_id = admin.id
            await unit_of_work.audit.add(
                actor_user_id=admin.id,
                action="master_public_link.updated",
                entity_type="master_public_link",
                entity_id=str(link.id),
                changes={"changed_fields": changed_fields},
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return MasterPublicLinkView.model_validate(link)

    async def delete_link(
        self,
        actor: AdminActor,
        link_id: int,
        *,
        correlation_id: str | None = None,
    ) -> None:
        ensure_admin(actor, self._admin_telegram_ids)
        async with self._unit_of_work_factory() as unit_of_work:
            admin = await unit_of_work.users.get_or_create_admin(actor)
            link = await unit_of_work.master_profile.get_link(link_id, for_update=True)
            if link is None:
                raise EntityNotFoundError("Ссылка больше не существует.")
            await unit_of_work.audit.add(
                actor_user_id=admin.id,
                action="master_public_link.deleted",
                entity_type="master_public_link",
                entity_id=str(link.id),
                changes={"label": link.label},
                correlation_id=correlation_id,
            )
            await unit_of_work.master_profile.delete_link(link)
            await unit_of_work.commit()

    @staticmethod
    async def _profile(
        unit_of_work: SqlAlchemyUnitOfWork, *, for_update: bool = False
    ) -> MasterProfile:
        profile = await unit_of_work.master_profile.get(for_update=for_update)
        if profile is None:
            raise RuntimeError("Master profile row is missing")
        return profile

    @staticmethod
    async def _view(
        unit_of_work: SqlAlchemyUnitOfWork,
        profile: MasterProfile,
        *,
        active_only: bool,
    ) -> MasterProfileView:
        links = await unit_of_work.master_profile.list_links(active_only=active_only)
        return MasterProfileView(
            id=profile.id,
            display_name=profile.display_name,
            bio=profile.bio,
            telegram_photo_file_id=profile.telegram_photo_file_id,
            telegram_photo_file_unique_id=profile.telegram_photo_file_unique_id,
            address=profile.address,
            map_url=profile.map_url,
            telegram_url=profile.telegram_url,
            is_published=profile.is_published,
            links=[MasterPublicLinkView.model_validate(link) for link in links],
        )
