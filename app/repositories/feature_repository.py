"""Tenant-scoped persistence for centralized feature flags."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import BusinessFeatureFlags
from app.domain.tenancy import DEFAULT_BUSINESS_ID
from app.repositories.scoped import TenantScopedRepository


class FeatureRepository(TenantScopedRepository):
    def __init__(self, session: AsyncSession, business_id: int = DEFAULT_BUSINESS_ID) -> None:
        super().__init__(session, business_id)

    async def get(self, *, for_update: bool = False) -> BusinessFeatureFlags | None:
        statement = select(BusinessFeatureFlags).where(
            BusinessFeatureFlags.business_id == self.business_id
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def flush(self) -> None:
        await self._session.flush()
