"""Authorized viewing and mutation of core business rules."""

from __future__ import annotations

from collections.abc import Callable

from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.service import AdminActor
from app.schemas.settings import BusinessSettingsPatch, BusinessSettingsView
from app.services.appointment_common import ensure_admin

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


class SettingsService:
    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        admin_telegram_ids: frozenset[int],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._admin_telegram_ids = admin_telegram_ids

    async def get(self, actor: AdminActor) -> BusinessSettingsView:
        ensure_admin(actor, self._admin_telegram_ids)
        async with self._unit_of_work_factory() as unit_of_work:
            settings = await unit_of_work.settings.get()
            if settings is None:
                raise RuntimeError("Business settings row is missing")
            return BusinessSettingsView.model_validate(settings)

    async def update(
        self,
        actor: AdminActor,
        patch: BusinessSettingsPatch,
        *,
        correlation_id: str | None = None,
    ) -> BusinessSettingsView:
        ensure_admin(actor, self._admin_telegram_ids)
        async with self._unit_of_work_factory() as unit_of_work:
            actor_user = await unit_of_work.users.get_or_create_admin(actor)
            settings = await unit_of_work.settings.get(for_update=True)
            if settings is None:
                raise RuntimeError("Business settings row is missing")
            changes = patch.model_dump(exclude_unset=True)
            field, new_value = next(iter(changes.items()))
            old_value = getattr(settings, field)
            setattr(settings, field, new_value)
            settings.version += 1
            await unit_of_work.session.flush()
            await unit_of_work.audit.add(
                actor_user_id=actor_user.id,
                action="business_settings.updated",
                entity_type="business_settings",
                entity_id="1",
                changes={
                    field: {"before": old_value, "after": new_value},
                    "version": settings.version,
                },
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return BusinessSettingsView.model_validate(settings)
