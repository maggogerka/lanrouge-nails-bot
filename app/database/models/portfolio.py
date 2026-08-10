"""Portfolio works, reusable Telegram media and descriptive tags."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.mixins import TimestampMixin
from app.database.types import database_enum
from app.domain.enums import MediaType, PortfolioStatus


class PortfolioItem(TimestampMixin, Base):
    """A master-created work that may be linked to a bookable service."""

    __tablename__ = "portfolio_items"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    business_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    staff_member_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("staff_members.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    linked_service_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("services.id", ondelete="RESTRICT"),
    )
    design_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    status: Mapped[PortfolioStatus] = mapped_column(
        database_enum(PortfolioStatus, name="portfolio_status"),
        nullable=False,
        default=PortfolioStatus.DRAFT,
        server_default=PortfolioStatus.DRAFT.value,
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint("design_price IS NULL OR design_price >= 0", name="design_price_valid"),
        Index(
            "ix_portfolio_items_business_staff_status_order",
            "business_id",
            "staff_member_id",
            "status",
            "sort_order",
            "published_at",
        ),
    )


class PortfolioMedia(Base):
    """A Telegram-hosted photo in a stable work-specific order."""

    __tablename__ = "portfolio_media"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    portfolio_item_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("portfolio_items.id", ondelete="RESTRICT"),
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("position >= 0", name="position_non_negative"),
        UniqueConstraint("portfolio_item_id", "position", name="uq_portfolio_media_position"),
        UniqueConstraint(
            "portfolio_item_id",
            "telegram_file_unique_id",
            name="uq_portfolio_media_file",
        ),
    )


class PortfolioTag(Base):
    """An administrator-managed portfolio classification."""

    __tablename__ = "portfolio_tags"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    business_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        Index("uq_portfolio_tags_business_slug", business_id, slug, unique=True),
        Index("uq_portfolio_tags_business_name_ci", business_id, func.lower(name), unique=True),
    )


class PortfolioItemTag(Base):
    """Many-to-many link between portfolio works and tags."""

    __tablename__ = "portfolio_item_tags"

    portfolio_item_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("portfolio_items.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    tag_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("portfolio_tags.id", ondelete="RESTRICT"),
        primary_key=True,
    )
