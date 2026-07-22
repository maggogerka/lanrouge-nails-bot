"""Persistence for the durable notification queue."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import NotificationJob
from app.domain.enums import NotificationJobStatus


class NotificationRepository:
    """Write notification jobs in their owning appointment transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_all(self, jobs: list[NotificationJob]) -> None:
        self._session.add_all(jobs)
        await self._session.flush()

    async def cancel_unsent(self, appointment_id: int) -> int:
        result = await self._session.execute(
            update(NotificationJob)
            .where(
                NotificationJob.appointment_id == appointment_id,
                NotificationJob.status.in_(
                    (NotificationJobStatus.PENDING, NotificationJobStatus.PROCESSING)
                ),
            )
            .values(
                status=NotificationJobStatus.CANCELLED,
                locked_at=None,
                locked_by=None,
            )
        )
        return int(cast(CursorResult[Any], result).rowcount or 0)
