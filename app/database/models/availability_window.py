"""Manual availability window model."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Text, func, text
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.mixins import TimestampMixin
from app.database.types import database_enum
from app.domain.enums import AvailabilityWindowStatus


class AvailabilityWindow(TimestampMixin, Base):
    """An indivisible interval offered to at most one client."""

    __tablename__ = "availability_windows"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    business_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    staff_member_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("staff_members.id", ondelete="RESTRICT"), nullable=False
    )
    service_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("services.id", ondelete="RESTRICT")
    )
    workstation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("workstations.id", ondelete="RESTRICT")
    )
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[AvailabilityWindowStatus] = mapped_column(
        database_enum(AvailabilityWindowStatus, name="availability_window_status"),
        nullable=False,
        default=AvailabilityWindowStatus.OPEN,
        server_default=AvailabilityWindowStatus.OPEN.value,
    )
    admin_comment: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint("start_at < end_at", name="positive_duration"),
        ExcludeConstraint(
            (staff_member_id, "="),
            (func.tstzrange(start_at, end_at, "[)"), "&&"),
            where=text("status IN ('open', 'reserved', 'booked')"),
            using="gist",
            name="ex_availability_windows_active_overlap",
        ),
        Index(
            "ix_availability_windows_business_staff_status_start",
            "business_id",
            "staff_member_id",
            "status",
            "start_at",
        ),
        Index("ix_availability_windows_business_start", "business_id", "start_at"),
        Index(
            "ix_availability_windows_business_service_start",
            "business_id",
            "service_id",
            "start_at",
        ),
    )
