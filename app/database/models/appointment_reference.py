"""Telegram-hosted reference photos attached to one appointment."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.types import database_enum
from app.domain.enums import MediaType


class AppointmentReferenceMedia(Base):
    """Ordered reference photo; binaries remain hosted by Telegram."""

    __tablename__ = "appointment_reference_media"
    __table_args__ = (
        CheckConstraint("position >= 0", name="position_non_negative"),
        UniqueConstraint("appointment_id", "position", name="uq_appointment_reference_position"),
        UniqueConstraint(
            "appointment_id",
            "telegram_file_unique_id",
            name="uq_appointment_reference_file",
        ),
        Index("ix_appointment_reference_active", "appointment_id", "deleted_at"),
        Index(
            "ix_appointment_reference_expiry",
            "expires_at",
            "id",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint(
            "(deleted_at IS NULL AND telegram_file_id IS NOT NULL "
            "AND telegram_file_unique_id IS NOT NULL) OR deleted_at IS NOT NULL",
            name="active_identifiers_present",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    business_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    appointment_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("appointments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    telegram_file_id: Mapped[str | None] = mapped_column(String(512))
    telegram_file_unique_id: Mapped[str | None] = mapped_column(String(255))
    media_type: Mapped[MediaType] = mapped_column(
        database_enum(MediaType, name="media_type"),
        nullable=False,
        default=MediaType.PHOTO,
        server_default=MediaType.PHOTO.value,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    uploaded_by_user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deletion_attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_deletion_error: Mapped[str | None] = mapped_column(String(100))


class ReferenceCleanupState(Base):
    """Per-business cleanup health state without sensitive identifiers."""

    __tablename__ = "reference_cleanup_state"
    __table_args__ = (
        CheckConstraint("consecutive_failures >= 0", name="failures_non_negative"),
        UniqueConstraint("business_id", name="business"),
    )

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    business_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_error_code: Mapped[str | None] = mapped_column(String(100))
