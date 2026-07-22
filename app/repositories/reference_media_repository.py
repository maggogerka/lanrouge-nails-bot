"""Persistence for ordered appointment reference photos."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AppointmentReferenceMedia


class ReferenceMediaRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add_all(self, media: list[AppointmentReferenceMedia]) -> None:
        self._session.add_all(media)
        await self._session.flush()

    async def add(self, media: AppointmentReferenceMedia) -> AppointmentReferenceMedia:
        self._session.add(media)
        await self._session.flush()
        return media

    async def list_active(self, appointment_id: int) -> list[AppointmentReferenceMedia]:
        rows = await self._session.scalars(
            select(AppointmentReferenceMedia)
            .where(
                AppointmentReferenceMedia.appointment_id == appointment_id,
                AppointmentReferenceMedia.deleted_at.is_(None),
            )
            .order_by(AppointmentReferenceMedia.position, AppointmentReferenceMedia.id)
        )
        return list(rows.all())
