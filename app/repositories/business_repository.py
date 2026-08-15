"""Tenant-scoped business profile persistence."""

from __future__ import annotations

from sqlalchemy import select

from app.database.models.business import Business
from app.repositories.scoped import TenantScopedRepository


class BusinessRepository(TenantScopedRepository):
    async def get(self, *, for_update: bool = False) -> Business | None:
        statement = select(Business).where(Business.id == self.business_id)
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def flush(self) -> None:
        await self._session.flush()
