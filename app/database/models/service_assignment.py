"""Business service categories and per-staff catalog overrides."""

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
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.mixins import TimestampMixin


class ServiceCategory(TimestampMixin, Base):
    """An ordered, archivable category within one business catalog."""

    __tablename__ = "service_categories"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    business_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("char_length(name) BETWEEN 1 AND 100", name="name_length_valid"),
        CheckConstraint("sort_order BETWEEN -100000 AND 100000", name="sort_order_valid"),
        Index(
            "uq_service_categories_business_name_ci",
            "business_id",
            func.lower(name),
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
        Index(
            "ix_service_categories_business_order",
            "business_id",
            "is_active",
            "sort_order",
            "id",
        ),
    )


class StaffServiceAssignment(TimestampMixin, Base):
    """A staff member's availability and optional commercial overrides for a service."""

    __tablename__ = "staff_service_assignments"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    business_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    staff_member_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("staff_members.id", ondelete="RESTRICT"), nullable=False
    )
    service_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("services.id", ondelete="RESTRICT"), nullable=False
    )
    price_override: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    duration_min_minutes_override: Mapped[int | None] = mapped_column(Integer)
    duration_max_minutes_override: Mapped[int | None] = mapped_column(Integer)
    prepayment_amount_override: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))
    prepayment_percent_override: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    online_booking_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("price_override IS NULL OR price_override >= 0", name="price_valid"),
        CheckConstraint(
            "(duration_min_minutes_override IS NULL "
            "AND duration_max_minutes_override IS NULL) OR "
            "(duration_min_minutes_override > 0 "
            "AND duration_max_minutes_override > 0 "
            "AND duration_min_minutes_override <= duration_max_minutes_override)",
            name="duration_override_valid",
        ),
        CheckConstraint(
            "prepayment_amount_override IS NULL OR prepayment_amount_override >= 0",
            name="prepayment_amount_valid",
        ),
        CheckConstraint(
            "prepayment_percent_override IS NULL OR prepayment_percent_override BETWEEN 0 AND 100",
            name="prepayment_percent_valid",
        ),
        CheckConstraint(
            "prepayment_amount_override IS NULL OR prepayment_percent_override IS NULL",
            name="single_prepayment_kind",
        ),
        CheckConstraint("sort_order BETWEEN -100000 AND 100000", name="sort_order_valid"),
        Index(
            "uq_staff_service_assignments_business_staff_service",
            "business_id",
            "staff_member_id",
            "service_id",
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
        Index(
            "ix_staff_service_assignments_bookable",
            "business_id",
            "service_id",
            "is_active",
            "online_booking_enabled",
            "staff_member_id",
            postgresql_where=text("archived_at IS NULL"),
        ),
    )
