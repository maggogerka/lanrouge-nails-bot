"""Client-authored reviews and publication moderation."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.mixins import TimestampMixin
from app.database.types import database_enum
from app.domain.enums import ReviewModerationStatus


class Review(TimestampMixin, Base):
    """One immutable client submission per completed appointment."""

    __tablename__ = "reviews"
    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 5", name="rating_valid"),
        CheckConstraint("text IS NULL OR char_length(text) <= 2000", name="text_length_valid"),
        Index("ix_reviews_moderation_created", "moderation_status", "created_at"),
        Index("ix_reviews_deleted_created", "deleted_at", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    appointment_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("appointments.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    client_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    publication_consent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    moderation_status: Mapped[ReviewModerationStatus] = mapped_column(
        database_enum(ReviewModerationStatus, name="review_moderation_status"),
        nullable=False,
        default=ReviewModerationStatus.PENDING,
        server_default=ReviewModerationStatus.PENDING.value,
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    edited_by_admin_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT")
    )
    is_admin_edited: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deleted_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT")
    )
    deletion_reason: Mapped[str | None] = mapped_column(Text)


class ReviewRevision(Base):
    """Append-only previous values captured before an administrator edit."""

    __tablename__ = "review_revisions"
    __table_args__ = (
        CheckConstraint("rating BETWEEN 1 AND 5", name="rating_valid"),
        CheckConstraint("text IS NULL OR char_length(text) <= 2000", name="text_length_valid"),
        Index("ix_review_revisions_review_created", "review_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    review_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("reviews.id", ondelete="RESTRICT"), nullable=False
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str | None] = mapped_column(Text)
    moderation_status: Mapped[ReviewModerationStatus] = mapped_column(
        database_enum(ReviewModerationStatus, name="review_moderation_status"), nullable=False
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    changed_by_admin_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
