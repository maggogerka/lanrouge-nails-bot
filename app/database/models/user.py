"""Telegram user persistence model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.mixins import TimestampMixin
from app.database.types import database_enum
from app.domain.enums import UserRole


class User(TimestampMixin, Base):
    """Minimal client/admin profile and consent state."""

    __tablename__ = "users"
    __table_args__ = (Index("ix_users_marketing_consent", "marketing_consent_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    username: Mapped[str | None] = mapped_column(String(64))
    first_name: Mapped[str | None] = mapped_column(String(255))
    last_name: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(32))
    role: Mapped[UserRole] = mapped_column(
        database_enum(UserRole, name="user_role"),
        nullable=False,
        default=UserRole.CLIENT,
        server_default=UserRole.CLIENT.value,
    )
    privacy_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    marketing_consent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    marketing_unsubscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    repeat_booking_opt_out_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_blocked: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    is_self_booking_blocked: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )
    self_booking_blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    self_booking_blocked_by: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
    )
    self_booking_block_reason: Mapped[str | None] = mapped_column(String(500))
