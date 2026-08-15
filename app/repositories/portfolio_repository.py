"""Persistence queries for portfolio works, media and tags."""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    PortfolioItem,
    PortfolioItemTag,
    PortfolioMedia,
    PortfolioTag,
    StaffMember,
)
from app.domain.enums import PortfolioStatus
from app.repositories.scoped import TenantScopedRepository


class PortfolioRepository(TenantScopedRepository):
    """Keep portfolio ordering, filtering and joins outside Telegram handlers."""

    def __init__(self, session: AsyncSession, business_id: int) -> None:
        super().__init__(session, business_id)

    async def get(self, item_id: int, *, for_update: bool = False) -> PortfolioItem | None:
        statement = select(PortfolioItem).where(
            PortfolioItem.id == item_id,
            PortfolioItem.business_id == self.business_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def add(self, item: PortfolioItem) -> PortfolioItem:
        self._require_business(item.business_id)
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
        staff_member_id: int | None = None,
    ) -> tuple[list[PortfolioItem], int]:
        filters = [PortfolioItem.business_id == self.business_id]
        if status is not None:
            filters.append(PortfolioItem.status == status)
        if staff_member_id is not None:
            filters.append(PortfolioItem.staff_member_id == staff_member_id)
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

    async def list_published_masters(self) -> list[StaffMember]:
        rows = await self._session.scalars(
            select(StaffMember)
            .join(PortfolioItem, PortfolioItem.staff_member_id == StaffMember.id)
            .where(
                StaffMember.business_id == self.business_id,
                StaffMember.is_active.is_(True),
                StaffMember.is_bookable.is_(True),
                StaffMember.archived_at.is_(None),
                PortfolioItem.business_id == self.business_id,
                PortfolioItem.status == PortfolioStatus.PUBLISHED,
            )
            .distinct()
            .order_by(StaffMember.sort_order, StaffMember.display_name, StaffMember.id)
        )
        return list(rows.all())

    async def list_media(self, item_id: int) -> list[PortfolioMedia]:
        result = await self._session.scalars(
            select(PortfolioMedia)
            .join(PortfolioItem, PortfolioItem.id == PortfolioMedia.portfolio_item_id)
            .where(PortfolioMedia.portfolio_item_id == item_id)
            .where(PortfolioItem.business_id == self.business_id)
            .order_by(PortfolioMedia.position, PortfolioMedia.id)
        )
        return list(result.all())

    async def add_media(self, media: list[PortfolioMedia]) -> None:
        item_ids = {item.portfolio_item_id for item in media}
        if item_ids:
            allowed = set(
                (
                    await self._session.scalars(
                        select(PortfolioItem.id).where(
                            PortfolioItem.id.in_(item_ids),
                            PortfolioItem.business_id == self.business_id,
                        )
                    )
                ).all()
            )
            if allowed != item_ids:
                raise ValueError("portfolio media parent belongs to another business")
        self._session.add_all(media)
        await self._session.flush()

    async def delete_media(self, item_id: int) -> None:
        await self._session.execute(
            delete(PortfolioMedia).where(
                PortfolioMedia.portfolio_item_id.in_(
                    select(PortfolioItem.id).where(
                        PortfolioItem.id == item_id,
                        PortfolioItem.business_id == self.business_id,
                    )
                )
            )
        )

    async def get_tag(self, tag_id: int) -> PortfolioTag | None:
        return (
            await self._session.scalars(
                select(PortfolioTag).where(
                    PortfolioTag.id == tag_id,
                    PortfolioTag.business_id == self.business_id,
                )
            )
        ).one_or_none()

    async def get_tag_by_name(self, name: str) -> PortfolioTag | None:
        return (
            await self._session.scalars(
                select(PortfolioTag).where(
                    PortfolioTag.business_id == self.business_id,
                    func.lower(PortfolioTag.name) == name.casefold(),
                )
            )
        ).one_or_none()

    async def get_tag_by_slug(self, slug: str) -> PortfolioTag | None:
        return (
            await self._session.scalars(
                select(PortfolioTag).where(
                    PortfolioTag.business_id == self.business_id,
                    PortfolioTag.slug == slug,
                )
            )
        ).one_or_none()

    async def list_tags(self, *, active_only: bool = True) -> list[PortfolioTag]:
        statement = select(PortfolioTag).where(PortfolioTag.business_id == self.business_id)
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
            .join(PortfolioItem, PortfolioItem.id == PortfolioItemTag.portfolio_item_id)
            .where(
                PortfolioItemTag.portfolio_item_id == item_id,
                PortfolioItem.business_id == self.business_id,
                PortfolioTag.business_id == self.business_id,
            )
            .order_by(func.lower(PortfolioTag.name), PortfolioTag.id)
        )
        return list(result.all())

    async def add_tag(self, tag: PortfolioTag) -> PortfolioTag:
        self._require_business(tag.business_id)
        self._session.add(tag)
        await self._session.flush()
        return tag

    async def replace_tags(self, item_id: int, tag_ids: set[int]) -> None:
        item_exists = await self._session.scalar(
            select(PortfolioItem.id).where(
                PortfolioItem.id == item_id,
                PortfolioItem.business_id == self.business_id,
            )
        )
        if item_exists is None:
            raise ValueError("portfolio item belongs to another business or is missing")
        if tag_ids:
            allowed_tags = set(
                (
                    await self._session.scalars(
                        select(PortfolioTag.id).where(
                            PortfolioTag.id.in_(tag_ids),
                            PortfolioTag.business_id == self.business_id,
                        )
                    )
                ).all()
            )
            if allowed_tags != tag_ids:
                raise ValueError("portfolio tag belongs to another business or is missing")
        await self._session.execute(
            delete(PortfolioItemTag).where(PortfolioItemTag.portfolio_item_id == item_id)
        )
        self._session.add_all(
            [PortfolioItemTag(portfolio_item_id=item_id, tag_id=tag_id) for tag_id in tag_ids]
        )
        await self._session.flush()
