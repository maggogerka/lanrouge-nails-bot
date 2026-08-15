"""Editable service catalog model."""

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
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.mixins import TimestampMixin


class Service(TimestampMixin, Base):
    """A service whose historical values are copied into appointments."""

    __tablename__ = "services"
    __table_args__ = (
        CheckConstraint("price >= 0", name="price_non_negative"),
        CheckConstraint("duration_min_minutes > 0", name="duration_min_positive"),
        CheckConstraint("duration_max_minutes > 0", name="duration_max_positive"),
        CheckConstraint(
            "duration_min_minutes <= duration_max_minutes",
            name="duration_range_valid",
        ),
        CheckConstraint("prepayment_amount >= 0", name="prepayment_non_negative"),
        CheckConstraint("prepayment_amount <= price", name="prepayment_within_price"),
        Index("ix_services_business_active_order", "business_id", "is_active", "sort_order"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    business_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    category_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("service_categories.id", ondelete="RESTRICT")
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    duration_min_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_max_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    prepayment_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, default=Decimal("0"), server_default="0"
    )
    online_booking_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    telegram_photo_file_id: Mapped[str | None] = mapped_column(String(512))
    telegram_photo_file_unique_id: Mapped[str | None] = mapped_column(String(255))
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
