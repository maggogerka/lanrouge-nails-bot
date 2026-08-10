"""One feature source used for visibility and fail-closed service guards."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from app.domain.errors import AuthorizationError, EntityNotFoundError, FeatureDisabledError
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.authorization import StaffContext, StaffPermission
from app.schemas.features import FeatureName, FeatureSnapshot

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


@dataclass(frozen=True, slots=True)
class FeaturePrerequisites:
    """Runtime-only readiness; credentials never enter feature persistence."""

    yookassa_ready: bool = False
    mini_app_ready: bool = False


class FeatureFlagService:
    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        prerequisites: FeaturePrerequisites | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._prerequisites = prerequisites or FeaturePrerequisites()

    async def snapshot(self) -> FeatureSnapshot:
        async with self._unit_of_work_factory() as unit_of_work:
            flags = await unit_of_work.features.get()
            if flags is None:
                raise EntityNotFoundError("Business feature settings are missing")
            return FeatureSnapshot.model_validate(flags)

    async def require_enabled(self, feature: FeatureName) -> FeatureSnapshot:
        snapshot = await self.snapshot()
        if not snapshot.enabled(feature):
            raise FeatureDisabledError("This bot feature is disabled by the business owner")
        return snapshot

    async def set_enabled(
        self,
        actor: StaffContext,
        feature: FeatureName,
        enabled: bool,
        *,
        correlation_id: str | None = None,
    ) -> FeatureSnapshot:
        if not actor.has_permission(StaffPermission.MANAGE_FEATURE_FLAGS):
            raise AuthorizationError("Only the business owner can change bot features")
        self._require_prerequisite(feature, enabled=enabled)
        async with self._unit_of_work_factory() as unit_of_work:
            if actor.business_id != unit_of_work.business_id:
                raise AuthorizationError("Cross-business feature update is forbidden")
            flags = await unit_of_work.features.get(for_update=True)
            if flags is None:
                raise EntityNotFoundError("Business feature settings are missing")
            setattr(flags, feature.value, enabled)
            await unit_of_work.features.flush()
            await unit_of_work.audit.add(
                actor_user_id=actor.user_id,
                action="feature_flag.changed",
                entity_type="business_feature_flags",
                entity_id=str(actor.business_id),
                changes={"feature": feature.value, "enabled": enabled},
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return FeatureSnapshot.model_validate(flags)

    def _require_prerequisite(self, feature: FeatureName, *, enabled: bool) -> None:
        if not enabled:
            return
        if feature is FeatureName.YOOKASSA_PAYMENTS and not self._prerequisites.yookassa_ready:
            raise FeatureDisabledError(
                "YooKassa нельзя включить: сначала настройте credentials на сервере."
            )
        if feature is FeatureName.MINI_APP and not self._prerequisites.mini_app_ready:
            raise FeatureDisabledError(
                "Mini App нельзя включить: сначала настройте HTTPS origins и ключи сессий."
            )
