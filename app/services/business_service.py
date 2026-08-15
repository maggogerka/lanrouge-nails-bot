"""Live-authorized white-label business setup and profile editing."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from app.database.models.business import Business
from app.domain.enums import BusinessStatus, BusinessType
from app.domain.errors import AuthorizationError, BusinessTypeTransitionError, EntityNotFoundError
from app.domain.welcome import default_welcome_html, sanitize_welcome_html
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.authorization import StaffContext, StaffPermission
from app.schemas.business import BusinessAdminView, BusinessProfileUpdate, BusinessWelcomeView
from app.services.authorization_service import AuthorizationService

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


class BusinessAdministrationService:
    """Persist public branding without storing Telegram or payment secrets."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        authorization_service: AuthorizationService,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._authorization = authorization_service

    async def get(self, actor: StaffContext) -> BusinessAdminView:
        live_actor = await self._authorize(actor)
        async with self._unit_of_work_factory() as unit_of_work:
            self._require_tenant(unit_of_work, live_actor)
            business = await unit_of_work.businesses.get()
            if business is None:
                raise EntityNotFoundError("Business was not found")
            return BusinessAdminView.model_validate(business)

    async def update(
        self,
        actor: StaffContext,
        values: BusinessProfileUpdate,
        *,
        correlation_id: str | None = None,
        now: datetime | None = None,
    ) -> BusinessAdminView:
        live_actor = await self._authorize(actor)
        current = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            self._require_tenant(unit_of_work, live_actor)
            business = await unit_of_work.businesses.get(for_update=True)
            if business is None:
                raise EntityNotFoundError("Business was not found")
            changes = values.model_dump(exclude_unset=True)
            if (
                changes.get("business_type") is BusinessType.SOLO
                and business.business_type is BusinessType.SALON
            ):
                bootstrap = await unit_of_work.staff.get_bootstrap_owner(
                    live_actor.business_id,
                    for_update=True,
                )
                if bootstrap is None:
                    raise BusinessTypeTransitionError(("bootstrap-владелец ещё не привязан",))
                blockers = await unit_of_work.staff.solo_transition_blockers(
                    live_actor.business_id,
                    bootstrap.id,
                    now=current,
                )
                if blockers:
                    raise BusinessTypeTransitionError(blockers)
            changed_fields = [
                field for field, value in changes.items() if getattr(business, field) != value
            ]
            for field in changed_fields:
                setattr(business, field, changes[field])
            legacy_fields = {"display_name", "timezone", "address", "map_url"}
            if legacy_fields.intersection(changed_fields):
                settings = await unit_of_work.settings.get(for_update=True)
                if settings is not None:
                    settings.business_name = business.display_name
                    settings.timezone = business.timezone
                    settings.address = business.address or "Адрес не указан"
                    settings.map_url = business.map_url or ""
                    settings.version += 1
            if (
                business.display_name
                and business.address
                and business.privacy_policy_url
                and business.setup_completed_at is None
            ):
                business.setup_completed_at = current
                business.status = BusinessStatus.ACTIVE
            await unit_of_work.businesses.flush()
            await unit_of_work.audit.add(
                actor_user_id=live_actor.user_id,
                action="business.profile_updated",
                entity_type="business",
                entity_id=str(live_actor.business_id),
                changes={"changed_fields": changed_fields},
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return BusinessAdminView.model_validate(business)

    async def set_bootstrap_bookable(
        self,
        actor: StaffContext,
        *,
        enabled: bool,
        correlation_id: str | None = None,
    ) -> StaffContext:
        """Toggle the bootstrap owner's specialist profile without changing authority."""

        live_actor = await self._authorize(actor)
        if not live_actor.is_bootstrap_owner:
            raise AuthorizationError("Только bootstrap-владелец может изменить свой профиль.")
        async with self._unit_of_work_factory() as unit_of_work:
            self._require_tenant(unit_of_work, live_actor)
            business = await unit_of_work.businesses.get(for_update=True)
            member = await unit_of_work.staff.get_by_id(
                live_actor.business_id,
                live_actor.staff_member_id,
                for_update=True,
            )
            if business is None or member is None or not member.is_bootstrap_owner:
                raise EntityNotFoundError("Bootstrap-профиль не найден.")
            if enabled and business.business_type is not BusinessType.SOLO:
                raise AuthorizationError(
                    "Отметить bootstrap специалистом можно в режиме «Один мастер»."
                )
            member.is_bookable = enabled
            await unit_of_work.staff.flush()
            await unit_of_work.audit.add(
                actor_user_id=live_actor.user_id,
                action="staff.bootstrap_bookable_changed",
                entity_type="staff_member",
                entity_id=str(member.id),
                changes={"is_bookable": enabled},
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return live_actor.model_copy(update={"is_bookable": enabled})

    async def get_welcome(self, actor: StaffContext) -> BusinessWelcomeView:
        live_actor = await self._authorize(actor)
        async with self._unit_of_work_factory() as unit_of_work:
            self._require_tenant(unit_of_work, live_actor)
            business = await unit_of_work.businesses.get()
            if business is None:
                raise EntityNotFoundError("Business was not found")
            return self._welcome_view(business)

    async def save_welcome_text(
        self,
        actor: StaffContext,
        text: str | None,
        *,
        correlation_id: str | None = None,
    ) -> BusinessWelcomeView:
        safe_text = sanitize_welcome_html(text) if text is not None else None
        live_actor = await self._authorize(actor)
        async with self._unit_of_work_factory() as unit_of_work:
            self._require_tenant(unit_of_work, live_actor)
            business = await unit_of_work.businesses.get(for_update=True)
            if business is None:
                raise EntityNotFoundError("Business was not found")
            business.welcome_draft_text = safe_text
            await unit_of_work.businesses.flush()
            await unit_of_work.audit.add(
                actor_user_id=live_actor.user_id,
                action="business.welcome_draft_text_updated",
                entity_type="business",
                entity_id=str(business.id),
                changes={
                    "uses_default": safe_text is None,
                    "text_length": len(safe_text or ""),
                },
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return self._welcome_view(business)

    async def save_welcome_photo(
        self,
        actor: StaffContext,
        *,
        file_id: str,
        file_unique_id: str,
        correlation_id: str | None = None,
    ) -> BusinessWelcomeView:
        if not 1 <= len(file_id) <= 512 or not 1 <= len(file_unique_id) <= 255:
            raise ValueError("welcome photo identifier has invalid length")
        live_actor = await self._authorize(actor)
        async with self._unit_of_work_factory() as unit_of_work:
            self._require_tenant(unit_of_work, live_actor)
            business = await unit_of_work.businesses.get(for_update=True)
            if business is None:
                raise EntityNotFoundError("Business was not found")
            business.welcome_draft_photo_file_id = file_id
            business.welcome_draft_photo_unique_id = file_unique_id
            await unit_of_work.businesses.flush()
            await unit_of_work.audit.add(
                actor_user_id=live_actor.user_id,
                action="business.welcome_draft_photo_updated",
                entity_type="business",
                entity_id=str(business.id),
                changes={"photo_present": True},
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return self._welcome_view(business)

    async def remove_welcome_photo(
        self,
        actor: StaffContext,
        *,
        correlation_id: str | None = None,
    ) -> BusinessWelcomeView:
        live_actor = await self._authorize(actor)
        async with self._unit_of_work_factory() as unit_of_work:
            self._require_tenant(unit_of_work, live_actor)
            business = await unit_of_work.businesses.get(for_update=True)
            if business is None:
                raise EntityNotFoundError("Business was not found")
            business.welcome_draft_photo_file_id = None
            business.welcome_draft_photo_unique_id = None
            await unit_of_work.businesses.flush()
            await unit_of_work.audit.add(
                actor_user_id=live_actor.user_id,
                action="business.welcome_draft_photo_removed",
                entity_type="business",
                entity_id=str(business.id),
                changes={"photo_present": False},
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return self._welcome_view(business)

    async def reset_welcome_draft(
        self,
        actor: StaffContext,
        *,
        correlation_id: str | None = None,
    ) -> BusinessWelcomeView:
        live_actor = await self._authorize(actor)
        async with self._unit_of_work_factory() as unit_of_work:
            self._require_tenant(unit_of_work, live_actor)
            business = await unit_of_work.businesses.get(for_update=True)
            if business is None:
                raise EntityNotFoundError("Business was not found")
            business.welcome_draft_text = None
            business.welcome_draft_photo_file_id = None
            business.welcome_draft_photo_unique_id = None
            await unit_of_work.businesses.flush()
            await unit_of_work.audit.add(
                actor_user_id=live_actor.user_id,
                action="business.welcome_draft_reset",
                entity_type="business",
                entity_id=str(business.id),
                changes={"uses_default": True, "photo_present": False},
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return self._welcome_view(business)

    async def publish_welcome(
        self,
        actor: StaffContext,
        *,
        correlation_id: str | None = None,
        now: datetime | None = None,
    ) -> BusinessWelcomeView:
        live_actor = await self._authorize(actor)
        changed_at = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            self._require_tenant(unit_of_work, live_actor)
            business = await unit_of_work.businesses.get(for_update=True)
            if business is None:
                raise EntityNotFoundError("Business was not found")
            business.welcome_published_text = business.welcome_draft_text
            business.welcome_published_photo_file_id = business.welcome_draft_photo_file_id
            business.welcome_published_photo_unique_id = business.welcome_draft_photo_unique_id
            business.welcome_published_at = changed_at
            await unit_of_work.businesses.flush()
            await unit_of_work.audit.add(
                actor_user_id=live_actor.user_id,
                action="business.welcome_published",
                entity_type="business",
                entity_id=str(business.id),
                changes={
                    "uses_default": business.welcome_published_text is None,
                    "photo_present": business.welcome_published_photo_file_id is not None,
                },
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return self._welcome_view(business)

    async def _authorize(self, actor: StaffContext) -> StaffContext:
        return await self._authorization.authorize(
            business_id=actor.business_id,
            telegram_id=actor.telegram_id,
            permission=StaffPermission.MANAGE_BUSINESS,
        )

    @staticmethod
    def _require_tenant(unit_of_work: SqlAlchemyUnitOfWork, actor: StaffContext) -> None:
        if unit_of_work.business_id != actor.business_id:
            raise RuntimeError("business unit of work tenant mismatch")

    @staticmethod
    def _aware_now(value: datetime | None) -> datetime:
        current = value or datetime.now(UTC)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        return current.astimezone(UTC)

    @staticmethod
    def _welcome_view(business: Business) -> BusinessWelcomeView:
        name = business.display_name
        fallback = default_welcome_html(name)
        return BusinessWelcomeView(
            draft_text=business.welcome_draft_text or fallback,
            draft_photo_file_id=business.welcome_draft_photo_file_id,
            published_text=business.welcome_published_text or fallback,
            published_photo_file_id=business.welcome_published_photo_file_id,
            published_at=business.welcome_published_at,
        )
