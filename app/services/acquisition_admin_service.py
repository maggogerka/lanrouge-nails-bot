"""Live-authorized acquisition source administration and aggregate statistics."""

from __future__ import annotations

import re
from collections.abc import Callable

from app.domain.acquisition import validate_campaign_code
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.acquisition import (
    AcquisitionLinkView,
    AcquisitionMetricsView,
    AcquisitionSourceView,
)
from app.schemas.authorization import StaffContext, StaffPermission
from app.services.acquisition_service import AcquisitionService
from app.services.authorization_service import AuthorizationService

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]
_BOT_USERNAME = re.compile(r"^[A-Za-z0-9_]{5,32}$")


class AcquisitionAdministrationService:
    """Expose PII-free funnels and safe public campaign identifiers."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        authorization_service: AuthorizationService,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._authorization = authorization_service

    async def list_metrics(self, actor: StaffContext) -> tuple[AcquisitionMetricsView, ...]:
        live_actor = await self._authorization.authorize(
            business_id=actor.business_id,
            telegram_id=actor.telegram_id,
            permission=StaffPermission.VIEW_ALL_STATISTICS,
        )
        async with self._unit_of_work_factory() as unit_of_work:
            self._require_tenant(unit_of_work, live_actor)
            rows = await unit_of_work.privacy.acquisition_metrics()
            return tuple(
                AcquisitionMetricsView(
                    source=AcquisitionSourceView.model_validate(row.source),
                    clients_arrived=row.clients_arrived,
                    clients_started_booking=row.clients_started_booking,
                    clients_completed_booking=row.clients_completed_booking,
                    repeat_clients=row.repeat_clients,
                )
                for row in rows
            )

    async def create_source(
        self,
        actor: StaffContext,
        *,
        code: str,
        display_name: str,
        channel: str | None = None,
        correlation_id: str | None = None,
    ) -> AcquisitionSourceView:
        live_actor = await self._authorization.authorize(
            business_id=actor.business_id,
            telegram_id=actor.telegram_id,
            permission=StaffPermission.MANAGE_BUSINESS,
        )
        async with self._unit_of_work_factory() as unit_of_work:
            self._require_tenant(unit_of_work, live_actor)
            source = await AcquisitionService(unit_of_work.privacy).create_source(
                raw_code=code,
                display_name=display_name,
                channel=channel,
                actor_staff_id=live_actor.staff_member_id,
            )
            await unit_of_work.audit.add(
                actor_user_id=live_actor.user_id,
                action="acquisition.source_created",
                entity_type="acquisition_source",
                entity_id=str(source.id),
                changes={"code": source.code, "channel": source.channel},
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return AcquisitionSourceView.model_validate(source)

    @staticmethod
    def link_for(source: AcquisitionSourceView, bot_username: str) -> AcquisitionLinkView:
        username = bot_username.removeprefix("@").strip()
        if _BOT_USERNAME.fullmatch(username) is None:
            raise ValueError("bot username is unavailable or invalid")
        code = validate_campaign_code(source.code)
        deep_link = f"https://t.me/{username}?start={code}"
        return AcquisitionLinkView(
            source=source,
            deep_link=deep_link,
            qr_payload=deep_link,
        )

    @staticmethod
    def _require_tenant(unit_of_work: SqlAlchemyUnitOfWork, actor: StaffContext) -> None:
        if unit_of_work.business_id != actor.business_id:
            raise RuntimeError("acquisition unit of work tenant mismatch")
