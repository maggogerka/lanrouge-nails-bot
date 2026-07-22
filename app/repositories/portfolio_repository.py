"""Persistence queries for portfolio works, media and tags."""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import PortfolioItem, PortfolioItemTag, PortfolioMedia, PortfolioTag
from app.domain.enums import PortfolioStatus


class PortfolioRepository:
    """Keep portfolio ordering, filtering and joins outside Telegram handlers."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, item_id: int, *, for_update: bool = False) -> PortfolioItem | None:
        statement = select(PortfolioItem).where(PortfolioItem.id == item_id)
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def add(self, item: PortfolioItem) -> PortfolioItem:
        self._session.add(item)
        await self._session.flush()
        return item

    async def list_page(
        self,
        *,
        status: PortfolioStatus | None,
        tag_id: int | None,
        limit: int,
        offset: int,
    ) -> tuple[list[PortfolioItem], int]:
        filters = []
        if status is not None:
            filters.append(PortfolioItem.status == status)
        items = select(PortfolioItem)
        count = select(func.count(PortfolioItem.id))
        if tag_id is not None:
            items = items.join(
                PortfolioItemTag,
                PortfolioItemTag.portfolio_item_id == PortfolioItem.id,
            )
            count = count.join(
                PortfolioItemTag,
                PortfolioItemTag.portfolio_item_id == PortfolioItem.id,
            )
            filters.append(PortfolioItemTag.tag_id == tag_id)
        items = (
            items.where(*filters)
            .order_by(
                PortfolioItem.sort_order,
                PortfolioItem.published_at.desc().nullslast(),
                PortfolioItem.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
        count = count.where(*filters)
        rows = list((await self._session.scalars(items)).all())
        return rows, int((await self._session.scalar(count)) or 0)

    async def list_media(self, item_id: int) -> list[PortfolioMedia]:
        result = await self._session.scalars(
            select(PortfolioMedia)
            .where(PortfolioMedia.portfolio_item_id == item_id)
            .order_by(PortfolioMedia.position, PortfolioMedia.id)
        )
        return list(result.all())

    async def add_media(self, media: list[PortfolioMedia]) -> None:
        self._session.add_all(media)
        await self._session.flush()

    async def delete_media(self, item_id: int) -> None:
        await self._session.execute(
            delete(PortfolioMedia).where(PortfolioMedia.portfolio_item_id == item_id)
        )

    async def get_tag(self, tag_id: int) -> PortfolioTag | None:
        return (
            await self._session.scalars(select(PortfolioTag).where(PortfolioTag.id == tag_id))
        ).one_or_none()

    async def list_tags(self, *, active_only: bool = True) -> list[PortfolioTag]:
        statement = select(PortfolioTag)
        if active_only:
            statement = statement.where(PortfolioTag.is_active.is_(True))
        result = await self._session.scalars(
            statement.order_by(func.lower(PortfolioTag.name), PortfolioTag.id)
        )
        return list(result.all())

    async def list_item_tags(self, item_id: int) -> list[PortfolioTag]:
        result = await self._session.scalars(
            select(PortfolioTag)
            .join(PortfolioItemTag, PortfolioItemTag.tag_id == PortfolioTag.id)
            .where(PortfolioItemTag.portfolio_item_id == item_id)
            .order_by(func.lower(PortfolioTag.name), PortfolioTag.id)
        )
        return list(result.all())

    async def add_tag(self, tag: PortfolioTag) -> PortfolioTag:
        self._session.add(tag)
        await self._session.flush()
        return tag

    async def replace_tags(self, item_id: int, tag_ids: set[int]) -> None:
        await self._session.execute(
            delete(PortfolioItemTag).where(PortfolioItemTag.portfolio_item_id == item_id)
        )
        self._session.add_all(
            [PortfolioItemTag(portfolio_item_id=item_id, tag_id=tag_id) for tag_id in tag_ids]
        )
        await self._session.flush()
