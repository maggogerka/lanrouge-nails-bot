"""Persistence for one review per appointment and moderation pages."""

from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.database.models import Review, ReviewRevision
from app.domain.enums import ReviewModerationStatus
from app.repositories.scoped import TenantScopedRepository


class ReviewRepository(TenantScopedRepository):
    def __init__(self, session: AsyncSession, business_id: int) -> None:
        super().__init__(session, business_id)

    async def get(self, review_id: int, *, for_update: bool = False) -> Review | None:
        statement = select(Review).where(
            Review.id == review_id,
            Review.business_id == self.business_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.scalars(statement)).one_or_none()

    async def get_for_appointment(self, appointment_id: int) -> Review | None:
        return (
            await self._session.scalars(
                select(Review).where(
                    Review.appointment_id == appointment_id,
                    Review.business_id == self.business_id,
                )
            )
        ).one_or_none()

    async def add(self, review: Review) -> Review:
        self._require_business(review.business_id)
        self._session.add(review)
        await self._session.flush()
        return review

    async def add_revision(self, revision: ReviewRevision) -> ReviewRevision:
        review_exists = await self._session.scalar(
            select(Review.id).where(
                Review.id == revision.review_id,
                Review.business_id == self.business_id,
            )
        )
        if review_exists is None:
            raise ValueError("review belongs to another business or is missing")
        self._session.add(revision)
        await self._session.flush()
        return revision

    async def list_page(
        self,
        *,
        moderation_status: ReviewModerationStatus | None,
        deleted_only: bool = False,
        limit: int,
        offset: int,
    ) -> tuple[list[Review], int]:
        filters: list[ColumnElement[bool]] = [
            Review.business_id == self.business_id,
            Review.deleted_at.is_not(None) if deleted_only else Review.deleted_at.is_(None),
        ]
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
            Review.business_id == self.business_id,
            Review.moderation_status == ReviewModerationStatus.APPROVED,
            Review.publication_consent.is_(True),
            Review.published_at.is_not(None),
            Review.deleted_at.is_(None),
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

    async def hard_delete(self, review: Review) -> None:
        self._require_business(review.business_id)
        await self._session.execute(
            delete(ReviewRevision).where(ReviewRevision.review_id == review.id)
        )
        await self._session.delete(review)
        await self._session.flush()
