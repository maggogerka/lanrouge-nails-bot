"""Persistence for the singleton master profile and public links."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import MasterProfile, MasterPublicLink


class MasterProfileRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, *, for_update: bool = False) -> MasterProfile | None:
        statement = select(MasterProfile).where(MasterProfile.id == 1)
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def get_link(self, link_id: int, *, for_update: bool = False) -> MasterPublicLink | None:
        statement = select(MasterPublicLink).where(MasterPublicLink.id == link_id)
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def list_links(self, *, active_only: bool) -> list[MasterPublicLink]:
        statement = select(MasterPublicLink).where(MasterPublicLink.profile_id == 1)
        if active_only:
            statement = statement.where(MasterPublicLink.is_active.is_(True))
        rows = await self._session.scalars(
            statement.order_by(MasterPublicLink.sort_order, MasterPublicLink.id)
        )
        return list(rows.all())

    async def add_link(self, link: MasterPublicLink) -> MasterPublicLink:
        self._session.add(link)
        await self._session.flush()
        return link

    async def delete_link(self, link: MasterPublicLink) -> None:
        await self._session.delete(link)
        await self._session.flush()
