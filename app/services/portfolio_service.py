"""Portfolio lifecycle, media validation and client-safe browsing."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from app.database.models import PortfolioItem, PortfolioItemTag, PortfolioMedia, PortfolioTag
from app.domain.enums import PortfolioDisplayMode, PortfolioStatus, StaffRole
from app.domain.errors import AuthorizationError, EntityNotFoundError, PortfolioStateError
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.booking import ClientActor
from app.schemas.pagination import PageRequest
from app.schemas.portfolio import (
    PortfolioCreate,
    PortfolioDisplayConfig,
    PortfolioDisplayUpdate,
    PortfolioItemView,
    PortfolioMasterView,
    PortfolioMediaView,
    PortfolioPage,
    PortfolioTagView,
)
from app.schemas.service import AdminActor
from app.security import get_staff_context
from app.services.appointment_common import ensure_admin

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


class PortfolioService:
    """Provide admin mutations and published-only client queries."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        admin_telegram_ids: frozenset[int],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._admin_telegram_ids = admin_telegram_ids

    async def get_max_media(self, actor: AdminActor) -> int:
        self._management_scope(actor)
        async with self._unit_of_work_factory() as unit_of_work:
            settings = await unit_of_work.settings.get()
            if settings is None:
                raise RuntimeError("Business settings row is missing")
            return settings.portfolio_max_media

    async def get_display_config(self) -> PortfolioDisplayConfig:
        async with self._unit_of_work_factory() as unit_of_work:
            settings = await unit_of_work.settings.get()
            if settings is None:
                raise RuntimeError("Business settings row is missing")
            return self._display_config(settings)

    async def update_display_config(
        self,
        actor: AdminActor,
        values: PortfolioDisplayUpdate,
        *,
        correlation_id: str | None = None,
    ) -> PortfolioDisplayConfig:
        ensure_admin(actor, self._admin_telegram_ids)
        async with self._unit_of_work_factory() as unit_of_work:
            admin = await unit_of_work.users.get_or_create_admin(actor)
            settings = await unit_of_work.settings.get(for_update=True)
            if settings is None:
                raise RuntimeError("Business settings row is missing")
            before = self._display_config(settings)
            changes = values.model_dump(exclude_unset=True)
            if "external_url" in changes:
                settings.external_portfolio_url = changes["external_url"]
            if "button_text" in changes:
                settings.external_portfolio_button_text = changes["button_text"]
            if "mode" in changes:
                settings.portfolio_mode = changes["mode"]
            if (
                settings.portfolio_mode is PortfolioDisplayMode.EXTERNAL_LINK
                and not settings.external_portfolio_url
            ):
                raise PortfolioStateError("Сначала укажите безопасную внешнюю ссылку портфолио.")
            settings.portfolio_enabled = (
                settings.portfolio_mode is not PortfolioDisplayMode.DISABLED
            )
            settings.version += 1
            after = self._display_config(settings)
            await unit_of_work.audit.add(
                actor_user_id=admin.id,
                action="portfolio.display_changed",
                entity_type="business_settings",
                entity_id="1",
                changes={
                    "mode": {"before": before.mode.value, "after": after.mode.value},
                    "external_url_configured": after.external_url is not None,
                    "button_text_changed": before.button_text != after.button_text,
                },
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return after

    async def create(
        self,
        actor: AdminActor,
        values: PortfolioCreate,
        *,
        publish: bool,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> PortfolioItemView:
        self._management_scope(actor, target_staff_member_id=values.staff_member_id)
        current_time = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            actor_user = await unit_of_work.users.get_or_create_admin(actor)
            settings = await unit_of_work.settings.get()
            if settings is None:
                raise RuntimeError("Business settings row is missing")
            if len(values.media) > settings.portfolio_max_media:
                raise PortfolioStateError(
                    f"Для одной работы разрешено не более {settings.portfolio_max_media} фото."
                )
            master = await unit_of_work.staff.get_by_id(
                unit_of_work.business_id,
                values.staff_member_id,
                for_update=True,
            )
            if master is None or not master.is_active or not master.is_bookable:
                raise EntityNotFoundError("Выбранный мастер больше недоступен.")
            if values.linked_service_id is not None:
                service = await unit_of_work.services.get(values.linked_service_id)
                if service is None:
                    raise EntityNotFoundError("Выбранная услуга больше не существует.")
                assignment = await unit_of_work.service_assignments.get_assignment(
                    unit_of_work.business_id,
                    values.staff_member_id,
                    values.linked_service_id,
                )
                if assignment is None or not assignment.is_active:
                    raise PortfolioStateError("Связанная услуга не назначена выбранному мастеру.")
            item = await unit_of_work.portfolio.add(
                PortfolioItem(
                    business_id=unit_of_work.business_id,
                    staff_member_id=values.staff_member_id,
                    title=values.title,
                    description=values.description,
                    linked_service_id=values.linked_service_id,
                    design_price=values.design_price,
                    status=PortfolioStatus.PUBLISHED if publish else PortfolioStatus.DRAFT,
                    sort_order=values.sort_order,
                    published_at=current_time if publish else None,
                    created_by=actor_user.id,
                )
            )
            await unit_of_work.portfolio.add_media(
                [
                    PortfolioMedia(
                        portfolio_item_id=item.id,
                        telegram_file_id=media.telegram_file_id,
                        telegram_file_unique_id=media.telegram_file_unique_id,
                        media_type=media.media_type,
                        position=position,
                    )
                    for position, media in enumerate(values.media)
                ]
            )
            tag_ids = await self._resolve_tags(unit_of_work, values.tag_names)
            if tag_ids:
                unit_of_work.session.add_all(
                    [
                        PortfolioItemTag(portfolio_item_id=item.id, tag_id=tag_id)
                        for tag_id in tag_ids
                    ]
                )
                await unit_of_work.session.flush()
            await unit_of_work.audit.add(
                actor_user_id=actor_user.id,
                action="portfolio.published" if publish else "portfolio.created",
                entity_type="portfolio_item",
                entity_id=str(item.id),
                changes={
                    "status": item.status.value,
                    "media_count": len(values.media),
                    "tag_count": len(tag_ids),
                    "linked_service_id": values.linked_service_id,
                    "staff_member_id": values.staff_member_id,
                },
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return await self._view(unit_of_work, item)

    async def list_admin(
        self,
        actor: AdminActor,
        page: PageRequest,
        *,
        status: PortfolioStatus | None = None,
    ) -> PortfolioPage:
        staff_member_id = self._management_scope(actor)
        async with self._unit_of_work_factory() as unit_of_work:
            items, total = await unit_of_work.portfolio.list_page(
                status=status,
                tag_id=None,
                limit=page.page_size,
                offset=page.offset,
                staff_member_id=staff_member_id,
            )
            return PortfolioPage(
                items=[await self._view(unit_of_work, item) for item in items],
                total=total,
                page=page.page,
                page_size=page.page_size,
            )

    async def get_admin(self, actor: AdminActor, item_id: int) -> PortfolioItemView:
        async with self._unit_of_work_factory() as unit_of_work:
            item = await unit_of_work.portfolio.get(item_id)
            if item is None:
                raise EntityNotFoundError("Работа больше не существует.")
            self._management_scope(actor, target_staff_member_id=item.staff_member_id)
            return await self._view(unit_of_work, item)

    async def list_published(
        self,
        actor: ClientActor,
        page: PageRequest,
        *,
        tag_id: int | None = None,
        staff_member_id: int | None = None,
    ) -> PortfolioPage:
        del actor
        async with self._unit_of_work_factory() as unit_of_work:
            settings = await unit_of_work.settings.get()
            if settings is None:
                raise RuntimeError("Business settings row is missing")
            if self._mode(settings) is not PortfolioDisplayMode.INTERNAL:
                raise PortfolioStateError("Портфолио временно недоступно.")
            items, total = await unit_of_work.portfolio.list_page(
                status=PortfolioStatus.PUBLISHED,
                tag_id=tag_id,
                limit=page.page_size,
                offset=page.offset,
                staff_member_id=staff_member_id,
            )
            return PortfolioPage(
                items=[await self._view(unit_of_work, item) for item in items],
                total=total,
                page=page.page,
                page_size=page.page_size,
            )

    async def list_published_masters(self) -> list[PortfolioMasterView]:
        async with self._unit_of_work_factory() as unit_of_work:
            rows = await unit_of_work.portfolio.list_published_masters()
            return [
                PortfolioMasterView(
                    staff_member_id=row.id,
                    display_name=row.display_name,
                    telegram_photo_file_id=row.telegram_photo_file_id,
                )
                for row in rows
            ]

    async def get_published(self, actor: ClientActor, item_id: int) -> PortfolioItemView:
        del actor
        async with self._unit_of_work_factory() as unit_of_work:
            settings = await unit_of_work.settings.get()
            if settings is None:
                raise RuntimeError("Business settings row is missing")
            if self._mode(settings) is not PortfolioDisplayMode.INTERNAL:
                raise PortfolioStateError("Портфолио временно недоступно.")
            item = await unit_of_work.portfolio.get(item_id)
            if item is None or item.status is not PortfolioStatus.PUBLISHED:
                raise EntityNotFoundError("Эта работа больше не опубликована.")
            return await self._view(unit_of_work, item)

    async def set_status(
        self,
        actor: AdminActor,
        item_id: int,
        status: PortfolioStatus,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> PortfolioItemView:
        async with self._unit_of_work_factory() as unit_of_work:
            actor_user = await unit_of_work.users.get_or_create_admin(actor)
            item = await unit_of_work.portfolio.get(item_id, for_update=True)
            if item is None:
                raise EntityNotFoundError("Работа больше не существует.")
            self._management_scope(actor, target_staff_member_id=item.staff_member_id)
            media = await unit_of_work.portfolio.list_media(item.id)
            if status is PortfolioStatus.PUBLISHED and not media:
                raise PortfolioStateError("Нельзя опубликовать работу без фотографии.")
            previous = item.status
            item.status = status
            if status is PortfolioStatus.PUBLISHED and item.published_at is None:
                item.published_at = self._aware_now(now)
            await unit_of_work.audit.add(
                actor_user_id=actor_user.id,
                action="portfolio.published"
                if status is PortfolioStatus.PUBLISHED
                else "portfolio.archived",
                entity_type="portfolio_item",
                entity_id=str(item.id),
                changes={"status": {"before": previous.value, "after": status.value}},
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return await self._view(unit_of_work, item)

    async def list_tags(self) -> list[PortfolioTagView]:
        async with self._unit_of_work_factory() as unit_of_work:
            tags = await unit_of_work.portfolio.list_tags(active_only=True)
            return [PortfolioTagView.model_validate(tag) for tag in tags]

    @staticmethod
    async def _resolve_tags(
        unit_of_work: SqlAlchemyUnitOfWork,
        names: list[str],
    ) -> set[int]:
        tag_ids: set[int] = set()
        for name in names:
            tag = await unit_of_work.portfolio.get_tag_by_name(name)
            if tag is None:
                base_slug = "-".join(
                    "".join(char.casefold() if char.isalnum() else " " for char in name).split()
                )
                base_slug = base_slug or "tag"
                slug = base_slug
                suffix = 2
                while await unit_of_work.portfolio.get_tag_by_slug(slug) is not None:
                    slug = f"{base_slug}-{suffix}"
                    suffix += 1
                tag = await unit_of_work.portfolio.add_tag(
                    PortfolioTag(
                        business_id=unit_of_work.business_id,
                        name=name,
                        slug=slug,
                        is_active=True,
                    )
                )
            elif not tag.is_active:
                raise PortfolioStateError(f"Тег «{name}» находится в архиве.")
            tag_ids.add(tag.id)
        return tag_ids

    @staticmethod
    async def _view(
        unit_of_work: SqlAlchemyUnitOfWork,
        item: PortfolioItem,
    ) -> PortfolioItemView:
        media = await unit_of_work.portfolio.list_media(item.id)
        tags = await unit_of_work.portfolio.list_item_tags(item.id)
        service = (
            await unit_of_work.services.get(item.linked_service_id)
            if item.linked_service_id is not None
            else None
        )
        master = await unit_of_work.staff.get_by_id(
            unit_of_work.business_id,
            item.staff_member_id,
        )
        return PortfolioItemView(
            id=item.id,
            title=item.title,
            description=item.description,
            linked_service_id=item.linked_service_id,
            linked_service_name=service.name if service is not None else None,
            design_price=item.design_price,
            status=item.status,
            sort_order=item.sort_order,
            published_at=item.published_at,
            media=[PortfolioMediaView.model_validate(value) for value in media],
            tags=[PortfolioTagView.model_validate(value) for value in tags],
            staff_member_id=item.staff_member_id,
            master_name=master.display_name if master is not None else None,
        )

    @staticmethod
    def _aware_now(value: datetime | None) -> datetime:
        current = value or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return current.astimezone(UTC)

    @staticmethod
    def _mode(settings: object) -> PortfolioDisplayMode:
        value = getattr(settings, "portfolio_mode", None)
        if value is None:
            return (
                PortfolioDisplayMode.INTERNAL
                if bool(getattr(settings, "portfolio_enabled", False))
                else PortfolioDisplayMode.DISABLED
            )
        return PortfolioDisplayMode(value)

    @classmethod
    def _display_config(cls, settings: object) -> PortfolioDisplayConfig:
        return PortfolioDisplayConfig(
            mode=cls._mode(settings),
            external_url=getattr(settings, "external_portfolio_url", None),
            button_text=(
                getattr(settings, "external_portfolio_button_text", None) or "Открыть портфолио"
            ),
        )

    def _management_scope(
        self,
        actor: AdminActor,
        *,
        target_staff_member_id: int | None = None,
    ) -> int | None:
        context = get_staff_context()
        if (
            context is not None
            and context.telegram_id == actor.telegram_id
            and context.role is StaffRole.MASTER
        ):
            if (
                target_staff_member_id is not None
                and target_staff_member_id != context.staff_member_id
            ):
                raise AuthorizationError("Мастер может изменять только своё портфолио.")
            return context.staff_member_id
        ensure_admin(actor, self._admin_telegram_ids)
        return target_staff_member_id
