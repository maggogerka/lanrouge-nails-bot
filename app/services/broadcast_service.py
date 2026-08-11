"""Administrator broadcast drafts, audience snapshots and lifecycle control."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from app.database.models import Broadcast, BroadcastMedia
from app.domain.enums import BroadcastStatus
from app.domain.errors import BroadcastStateError, EntityNotFoundError
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.broadcast import (
    BroadcastCreate,
    BroadcastMediaView,
    BroadcastResult,
    BroadcastView,
)
from app.schemas.pagination import Page, PageRequest
from app.schemas.service import AdminActor
from app.services.appointment_common import ensure_admin

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


class BroadcastService:
    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        admin_telegram_ids: frozenset[int],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._admin_telegram_ids = admin_telegram_ids

    async def create_draft(
        self,
        actor: AdminActor,
        values: BroadcastCreate,
        *,
        correlation_id: str | None = None,
    ) -> BroadcastView:
        self._ensure_admin(actor)
        async with self._unit_of_work_factory() as uow:
            admin = await uow.users.get_or_create_admin(actor)
            settings = await uow.settings.get()
            if settings is None or not settings.broadcasts_enabled:
                raise BroadcastStateError("Рассылки отключены в настройках бизнеса.")
            if len(values.media) > settings.broadcast_max_media:
                raise BroadcastStateError(
                    f"К рассылке можно прикрепить не более {settings.broadcast_max_media} фото."
                )
            if values.linked_portfolio_item_id is not None:
                portfolio = await uow.portfolio.get(values.linked_portfolio_item_id)
                if portfolio is None:
                    raise EntityNotFoundError("Связанная работа портфолио не найдена.")
            broadcast = await uow.broadcasts.add(
                Broadcast(
                    business_id=uow.business_id,
                    title=values.title,
                    text=values.text,
                    parse_mode=None,
                    status=BroadcastStatus.DRAFT,
                    audience_type=values.audience_type,
                    audience_parameters=values.audience_parameters,
                    button_type=values.button_type,
                    button_text=values.button_text,
                    button_url=str(values.button_url) if values.button_url else None,
                    linked_portfolio_item_id=values.linked_portfolio_item_id,
                    created_by=admin.id,
                )
            )
            await uow.broadcasts.add_media(
                [
                    BroadcastMedia(
                        broadcast_id=broadcast.id,
                        telegram_file_id=item.telegram_file_id,
                        telegram_file_unique_id=item.telegram_file_unique_id,
                        media_type=item.media_type,
                        position=position,
                    )
                    for position, item in enumerate(values.media)
                ]
            )
            await uow.audit.add(
                actor_user_id=admin.id,
                action="broadcast.draft_created",
                entity_type="broadcast",
                entity_id=str(broadcast.id),
                changes={
                    "audience_type": broadcast.audience_type.value,
                    "media_count": len(values.media),
                    "button_type": broadcast.button_type.value,
                },
                correlation_id=correlation_id,
            )
            media = await uow.broadcasts.list_media(broadcast.id)
            await uow.commit()
            return self._view(broadcast, media)

    async def estimate_audience(
        self,
        actor: AdminActor,
        broadcast_id: int,
        *,
        now: datetime | None = None,
    ) -> int:
        self._ensure_admin(actor)
        current = self._aware_now(now)
        async with self._unit_of_work_factory() as uow:
            broadcast = await self._broadcast(uow, broadcast_id)
            return len(
                await uow.broadcasts.resolve_audience_user_ids(
                    audience_type=broadcast.audience_type,
                    parameters=broadcast.audience_parameters,
                    now=current,
                )
            )

    async def launch(
        self,
        actor: AdminActor,
        broadcast_id: int,
        *,
        confirmed: bool,
        scheduled_at: datetime | None = None,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> BroadcastResult:
        self._ensure_admin(actor)
        if not confirmed:
            raise BroadcastStateError("Перед массовой отправкой требуется подтверждение.")
        current = self._aware_now(now)
        schedule = self._aware_now(scheduled_at) if scheduled_at else current
        if schedule < current:
            raise BroadcastStateError("Нельзя запланировать рассылку в прошлом.")
        async with self._unit_of_work_factory() as uow:
            admin = await uow.users.get_or_create_admin(actor)
            broadcast = await self._broadcast(uow, broadcast_id, for_update=True)
            if broadcast.status is not BroadcastStatus.DRAFT:
                raise BroadcastStateError("Эта рассылка уже запускалась.")
            user_ids = await uow.broadcasts.resolve_audience_user_ids(
                audience_type=broadcast.audience_type,
                parameters=broadcast.audience_parameters,
                now=current,
            )
            if not user_ids:
                raise BroadcastStateError("В выбранной аудитории нет подписанных клиентов.")
            frozen = await uow.broadcasts.freeze_recipients(
                broadcast_id=broadcast.id,
                user_ids=user_ids,
                scheduled_at=schedule,
            )
            broadcast.status = BroadcastStatus.SCHEDULED
            broadcast.scheduled_at = schedule
            await uow.audit.add(
                actor_user_id=admin.id,
                action="broadcast.launched",
                entity_type="broadcast",
                entity_id=str(broadcast.id),
                changes={"recipient_count": frozen, "scheduled_at": schedule.isoformat()},
                correlation_id=correlation_id,
            )
            media = await uow.broadcasts.list_media(broadcast.id)
            counts = await uow.broadcasts.status_counts(broadcast.id)
            await uow.commit()
            return BroadcastResult(
                broadcast=self._view(broadcast, media), total=frozen, counts=counts
            )

    async def cancel(
        self,
        actor: AdminActor,
        broadcast_id: int,
        *,
        correlation_id: str | None = None,
    ) -> BroadcastResult:
        self._ensure_admin(actor)
        async with self._unit_of_work_factory() as uow:
            admin = await uow.users.get_or_create_admin(actor)
            broadcast = await self._broadcast(uow, broadcast_id, for_update=True)
            if broadcast.status in {
                BroadcastStatus.COMPLETED,
                BroadcastStatus.PARTIALLY_FAILED,
                BroadcastStatus.CANCELLED,
                BroadcastStatus.FAILED,
            }:
                raise BroadcastStateError("Рассылка уже завершена.")
            skipped = await uow.broadcasts.cancel_open_recipients(broadcast.id)
            broadcast.status = BroadcastStatus.CANCELLED
            broadcast.finished_at = datetime.now(UTC)
            await uow.audit.add(
                actor_user_id=admin.id,
                action="broadcast.cancelled",
                entity_type="broadcast",
                entity_id=str(broadcast.id),
                changes={"skipped_count": skipped},
                correlation_id=correlation_id,
            )
            result = await self._result(uow, broadcast)
            await uow.commit()
            return result

    async def get_result(self, actor: AdminActor, broadcast_id: int) -> BroadcastResult:
        self._ensure_admin(actor)
        async with self._unit_of_work_factory() as uow:
            return await self._result(uow, await self._broadcast(uow, broadcast_id))

    async def list_broadcasts(
        self,
        actor: AdminActor,
        *,
        status: BroadcastStatus | None = None,
        page: PageRequest | None = None,
    ) -> Page[BroadcastView]:
        self._ensure_admin(actor)
        page = page or PageRequest()
        async with self._unit_of_work_factory() as uow:
            rows, total = await uow.broadcasts.list_page(
                status=status, limit=page.page_size, offset=page.offset
            )
            return Page(
                items=[self._view(row, await uow.broadcasts.list_media(row.id)) for row in rows],
                total=total,
                page=page.page,
                page_size=page.page_size,
            )

    async def _result(self, uow: SqlAlchemyUnitOfWork, broadcast: Broadcast) -> BroadcastResult:
        counts = await uow.broadcasts.status_counts(broadcast.id)
        return BroadcastResult(
            broadcast=self._view(broadcast, await uow.broadcasts.list_media(broadcast.id)),
            total=sum(counts.values()),
            counts=counts,
        )

    @staticmethod
    async def _broadcast(
        uow: SqlAlchemyUnitOfWork, broadcast_id: int, *, for_update: bool = False
    ) -> Broadcast:
        broadcast = await uow.broadcasts.get(broadcast_id, for_update=for_update)
        if broadcast is None:
            raise EntityNotFoundError("Рассылка не найдена.")
        return broadcast

    @staticmethod
    def _view(broadcast: Broadcast, media: list[BroadcastMedia]) -> BroadcastView:
        return BroadcastView(
            id=broadcast.id,
            title=broadcast.title,
            text=broadcast.text,
            status=broadcast.status,
            audience_type=broadcast.audience_type,
            audience_parameters=broadcast.audience_parameters,
            button_type=broadcast.button_type,
            button_text=broadcast.button_text,
            button_url=broadcast.button_url,
            linked_portfolio_item_id=broadcast.linked_portfolio_item_id,
            scheduled_at=broadcast.scheduled_at,
            started_at=broadcast.started_at,
            finished_at=broadcast.finished_at,
            media=[
                BroadcastMediaView(
                    telegram_file_id=item.telegram_file_id,
                    telegram_file_unique_id=item.telegram_file_unique_id,
                    media_type=item.media_type,
                    position=item.position,
                )
                for item in media
            ],
            created_at=broadcast.created_at,
        )

    def _ensure_admin(self, actor: AdminActor) -> None:
        ensure_admin(actor, self._admin_telegram_ids)

    @staticmethod
    def _aware_now(value: datetime | None) -> datetime:
        current = value or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("datetime must be timezone-aware")
        return current.astimezone(UTC)
