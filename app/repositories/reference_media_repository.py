"""Persistence for ordered appointment reference photos."""

from __future__ import annotations

from datetime import datetime
from typing import Any, cast

from sqlalchemy import select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AppointmentReferenceMedia, ReferenceCleanupState


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

    async def get(
        self, media_id: int, *, for_update: bool = False
    ) -> AppointmentReferenceMedia | None:
        statement = select(AppointmentReferenceMedia).where(
            AppointmentReferenceMedia.id == media_id
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def list_expired(
        self,
        now: datetime,
        *,
        limit: int = 1000,
    ) -> list[AppointmentReferenceMedia]:
        rows = await self._session.scalars(
            select(AppointmentReferenceMedia)
            .where(
                AppointmentReferenceMedia.deleted_at.is_(None),
                AppointmentReferenceMedia.expires_at <= now,
            )
            .order_by(AppointmentReferenceMedia.expires_at, AppointmentReferenceMedia.id)
            .limit(limit)
        )
        return list(rows.all())

    async def set_expiry_for_appointment(
        self,
        appointment_id: int,
        expires_at: datetime,
    ) -> int:
        result = await self._session.execute(
            update(AppointmentReferenceMedia)
            .where(
                AppointmentReferenceMedia.appointment_id == appointment_id,
                AppointmentReferenceMedia.deleted_at.is_(None),
            )
            .values(expires_at=expires_at)
        )
        return int(cast(CursorResult[Any], result).rowcount or 0)

    async def move_active(
        self,
        source_appointment_id: int,
        target_appointment_id: int,
        expires_at: datetime,
    ) -> int:
        result = await self._session.execute(
            update(AppointmentReferenceMedia)
            .where(
                AppointmentReferenceMedia.appointment_id == source_appointment_id,
                AppointmentReferenceMedia.deleted_at.is_(None),
            )
            .values(appointment_id=target_appointment_id, expires_at=expires_at)
        )
        return int(cast(CursorResult[Any], result).rowcount or 0)

    async def get_cleanup_state(self, *, for_update: bool = False) -> ReferenceCleanupState:
        statement = select(ReferenceCleanupState).where(ReferenceCleanupState.id == 1)
        if for_update:
            statement = statement.with_for_update()
        state = (await self._session.scalars(statement)).one_or_none()
        if state is None:
            raise RuntimeError("Reference cleanup state row is missing")
        return state
