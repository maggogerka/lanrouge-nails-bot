"""Editable service catalog model."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Index, Integer, Numeric, String, Text
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
        Index("ix_services_active_name", "is_active", "name"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    duration_min_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    duration_max_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        server_default="true",
    )
