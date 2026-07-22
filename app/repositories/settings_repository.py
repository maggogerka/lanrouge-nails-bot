"""Persistence for the singleton business settings row."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import BusinessSettings


class SettingsRepository:
    """Read or lock the typed singleton settings."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, *, for_update: bool = False) -> BusinessSettings | None:
        statement = select(BusinessSettings).where(BusinessSettings.id == 1)
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.scalars(statement)
        return result.one_or_none()
