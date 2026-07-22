"""Persistence for one review per appointment and moderation pages."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Review
from app.domain.enums import ReviewModerationStatus


class ReviewRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, review_id: int, *, for_update: bool = False) -> Review | None:
        statement = select(Review).where(Review.id == review_id)
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def get_for_appointment(self, appointment_id: int) -> Review | None:
        return (
            await self._session.scalars(
                select(Review).where(Review.appointment_id == appointment_id)
            )
        ).one_or_none()

    async def add(self, review: Review) -> Review:
        self._session.add(review)
        await self._session.flush()
        return review

    async def list_page(
        self,
        *,
        moderation_status: ReviewModerationStatus | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Review], int]:
        filters = []
        if moderation_status is not None:
            filters.append(Review.moderation_status == moderation_status)
        rows = (
            select(Review)
            .where(*filters)
            .order_by(Review.created_at.desc(), Review.id.desc())
            .limit(limit)
            .offset(offset)
        )
        count = select(func.count(Review.id)).where(*filters)
        return list((await self._session.scalars(rows)).all()), int(
            (await self._session.scalar(count)) or 0
        )

    async def list_published(self, *, limit: int, offset: int) -> tuple[list[Review], int]:
        filters = [
            Review.moderation_status == ReviewModerationStatus.APPROVED,
            Review.publication_consent.is_(True),
            Review.published_at.is_not(None),
        ]
        rows = (
            select(Review)
            .where(*filters)
            .order_by(Review.published_at.desc(), Review.id.desc())
            .limit(limit)
            .offset(offset)
        )
        count = select(func.count(Review.id)).where(*filters)
        return list((await self._session.scalars(rows)).all()), int(
            (await self._session.scalar(count)) or 0
        )
