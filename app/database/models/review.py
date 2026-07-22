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
