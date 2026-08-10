"""Persistence for the singleton master profile and public links."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import MasterProfile, MasterPublicLink
from app.domain.tenancy import DEFAULT_STAFF_MEMBER_ID
from app.repositories.scoped import TenantScopedRepository


class MasterProfileRepository(TenantScopedRepository):
    def __init__(self, session: AsyncSession, business_id: int) -> None:
        super().__init__(session, business_id)

    async def get(
        self,
        *,
        staff_member_id: int = DEFAULT_STAFF_MEMBER_ID,
        for_update: bool = False,
    ) -> MasterProfile | None:
        statement = select(MasterProfile).where(
            MasterProfile.business_id == self.business_id,
            MasterProfile.staff_member_id == staff_member_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def get_link(self, link_id: int, *, for_update: bool = False) -> MasterPublicLink | None:
        statement = select(MasterPublicLink).where(
            MasterPublicLink.id == link_id,
            MasterPublicLink.business_id == self.business_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def list_links(self, *, active_only: bool) -> list[MasterPublicLink]:
        statement = (
            select(MasterPublicLink)
            .join(MasterProfile, MasterProfile.id == MasterPublicLink.profile_id)
            .where(
                MasterPublicLink.business_id == self.business_id,
                MasterProfile.business_id == self.business_id,
                MasterProfile.staff_member_id == DEFAULT_STAFF_MEMBER_ID,
            )
        )
        if active_only:
            statement = statement.where(MasterPublicLink.is_active.is_(True))
        rows = await self._session.scalars(
            statement.order_by(MasterPublicLink.sort_order, MasterPublicLink.id)
        )
        return list(rows.all())

    async def add_link(self, link: MasterPublicLink) -> MasterPublicLink:
        self._require_business(link.business_id)
        self._session.add(link)
        await self._session.flush()
        return link

    async def delete_link(self, link: MasterPublicLink) -> None:
        self._require_business(link.business_id)
        await self._session.delete(link)
        await self._session.flush()
