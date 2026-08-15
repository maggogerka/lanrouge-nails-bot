"""Physical workstations and their compatible services."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, String, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.mixins import TimestampMixin


class Workstation(TimestampMixin, Base):
    """One physical place that cannot serve overlapping active windows."""

    __tablename__ = "workstations"
    __table_args__ = (
        Index(
            "uq_workstations_business_name_ci",
            "business_id",
            func.lower(text("name")),
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
        Index(
            "ix_workstations_business_active_order",
            "business_id",
            "is_active",
            "sort_order",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    business_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class WorkstationService(TimestampMixin, Base):
    """A reversible service compatibility assignment for a workstation."""

    __tablename__ = "workstation_services"
    __table_args__ = (
        Index(
            "ix_workstation_services_business_service_active",
            "business_id",
            "service_id",
            "is_active",
            "workstation_id",
        ),
    )

    workstation_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("workstations.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    service_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("services.id", ondelete="RESTRICT"),
        primary_key=True,
    )
    business_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
