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
    String,
    UniqueConstraint,
    func,
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
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    appointment_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("appointments.id", ondelete="RESTRICT"),
        nullable=False,
    )
    telegram_file_id: Mapped[str] = mapped_column(String(512), nullable=False)
    telegram_file_unique_id: Mapped[str] = mapped_column(String(255), nullable=False)
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
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
