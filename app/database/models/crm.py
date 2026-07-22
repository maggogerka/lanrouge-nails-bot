"""Client tags, private notes and append-only consent history."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.mixins import TimestampMixin
from app.database.types import database_enum
from app.domain.enums import ConsentSource, ConsentType


class ClientTag(TimestampMixin, Base):
    """An administrator-owned CRM label, never a system status."""

    __tablename__ = "client_tags"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    marker: Mapped[str | None] = mapped_column(String(32))
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    __table_args__ = (Index("uq_client_tags_name_ci", func.lower(name), unique=True),)


class UserClientTag(Base):
    """Administrator attribution for assigning a CRM tag to a client."""

    __tablename__ = "user_client_tags"

    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), primary_key=True
    )
    tag_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("client_tags.id", ondelete="RESTRICT"), primary_key=True
    )
    assigned_by: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ClientNote(TimestampMixin, Base):
    """A private operational note that must never be exposed or logged verbatim."""

    __tablename__ = "client_notes"
    __table_args__ = (
        CheckConstraint("char_length(text) BETWEEN 1 AND 2000", name="text_length_valid"),
        Index("ix_client_notes_client_created", "client_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    client_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    author_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConsentHistory(Base):
    """Append-only proof of independent consent preference changes."""

    __tablename__ = "consent_history"
    __table_args__ = (Index("ix_consent_history_user_created", "user_id", "created_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    consent_type: Mapped[ConsentType] = mapped_column(
        database_enum(ConsentType, name="consent_type"), nullable=False
    )
    previous_value: Mapped[bool | None] = mapped_column(Boolean)
    new_value: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source: Mapped[ConsentSource] = mapped_column(
        database_enum(ConsentSource, name="consent_source"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
