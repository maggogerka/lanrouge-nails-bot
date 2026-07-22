"""Persistence for the durable notification queue."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import NotificationJob


class NotificationRepository:
    """Write notification jobs in their owning appointment transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_all(self, jobs: list[NotificationJob]) -> None:
        self._session.add_all(jobs)
        await self._session.flush()
