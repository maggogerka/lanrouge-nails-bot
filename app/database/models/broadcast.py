"""Broadcast campaigns, frozen recipients and safe interaction events."""

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
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    text as sql_text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.mixins import TimestampMixin
from app.database.types import database_enum
from app.domain.enums import (
    BroadcastAudienceType,
    BroadcastButtonType,
    BroadcastRecipientStatus,
    BroadcastStatus,
    MarketingEventType,
    MediaType,
)


class Broadcast(TimestampMixin, Base):
    """An administrator-confirmed marketing campaign."""

    __tablename__ = "broadcasts"
    __table_args__ = (Index("ix_broadcasts_status_schedule", "status", "scheduled_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    parse_mode: Mapped[str | None] = mapped_column(String(16))
    status: Mapped[BroadcastStatus] = mapped_column(
        database_enum(BroadcastStatus, name="broadcast_status"),
        nullable=False,
        default=BroadcastStatus.DRAFT,
        server_default=BroadcastStatus.DRAFT.value,
    )
    audience_type: Mapped[BroadcastAudienceType] = mapped_column(
        database_enum(BroadcastAudienceType, name="broadcast_audience_type"), nullable=False
    )
    audience_parameters: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=sql_text("'{}'::jsonb")
    )
    button_type: Mapped[BroadcastButtonType] = mapped_column(
        database_enum(BroadcastButtonType, name="broadcast_button_type"),
        nullable=False,
        default=BroadcastButtonType.NONE,
        server_default=BroadcastButtonType.NONE.value,
    )
    button_text: Mapped[str | None] = mapped_column(String(100))
    button_url: Mapped[str | None] = mapped_column(String(2048))
    linked_portfolio_item_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("portfolio_items.id", ondelete="RESTRICT")
    )
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )


class BroadcastMedia(Base):
    """A Telegram-hosted photo attached to a campaign."""

    __tablename__ = "broadcast_media"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    broadcast_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("broadcasts.id", ondelete="RESTRICT"), nullable=False
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("position >= 0", name="position_non_negative"),
        UniqueConstraint("broadcast_id", "position", name="uq_broadcast_media_position"),
        UniqueConstraint("broadcast_id", "telegram_file_unique_id", name="uq_broadcast_media_file"),
    )


class BroadcastRecipient(TimestampMixin, Base):
    """Frozen audience membership and retryable delivery state."""

    __tablename__ = "broadcast_recipients"
    __table_args__ = (
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        UniqueConstraint("broadcast_id", "user_id", name="uq_broadcast_recipients_user"),
        Index("ix_broadcast_recipients_due", "status", "available_at"),
        Index("ix_broadcast_recipients_broadcast_status", "broadcast_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    broadcast_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("broadcasts.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[BroadcastRecipientStatus] = mapped_column(
        database_enum(BroadcastRecipientStatus, name="broadcast_recipient_status"),
        nullable=False,
        default=BroadcastRecipientStatus.PENDING,
        server_default=BroadcastRecipientStatus.PENDING.value,
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    available_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[str | None] = mapped_column(String(128))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(1000))
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger)


class MarketingEvent(Base):
    """A minimal callback interaction, not an unreliable message-open event."""

    __tablename__ = "marketing_events"
    __table_args__ = (Index("ix_marketing_events_broadcast_created", "broadcast_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    broadcast_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("broadcasts.id", ondelete="RESTRICT"), nullable=False
    )
    event_type: Mapped[MarketingEventType] = mapped_column(
        database_enum(MarketingEventType, name="marketing_event_type"), nullable=False
    )
    event_data: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict, server_default=sql_text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
