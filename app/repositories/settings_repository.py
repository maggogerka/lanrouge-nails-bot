"""Persistence for the singleton business settings row."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import BusinessSettings
from app.domain.tenancy import DEFAULT_BUSINESS_ID
from app.repositories.scoped import TenantScopedRepository


class SettingsRepository(TenantScopedRepository):
    """Read or lock the typed singleton settings."""

    def __init__(self, session: AsyncSession, business_id: int = DEFAULT_BUSINESS_ID) -> None:
        super().__init__(session, business_id)

    async def get(self, *, for_update: bool = False) -> BusinessSettings | None:
        statement = select(BusinessSettings).where(BusinessSettings.business_id == self.business_id)
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.scalars(statement)
        return result.one_or_none()
