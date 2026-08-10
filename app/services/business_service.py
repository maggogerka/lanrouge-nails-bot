"""Live-authorized white-label business setup and profile editing."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from app.domain.enums import BusinessStatus
from app.domain.errors import EntityNotFoundError
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.authorization import StaffContext, StaffPermission
from app.schemas.business import BusinessAdminView, BusinessProfileUpdate
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
            changed_fields = [
                field for field, value in changes.items() if getattr(business, field) != value
            ]
            for field in changed_fields:
                setattr(business, field, changes[field])
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
