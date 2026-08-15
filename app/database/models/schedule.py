"""Per-staff weekly schedules and date-specific exceptions."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.mixins import TimestampMixin
from app.database.types import database_enum
from app.domain.enums import ScheduleExceptionKind, ScheduleIntervalKind


class StaffWeeklyInterval(TimestampMixin, Base):
    """One recurring local-time work or break interval for a staff member."""

    __tablename__ = "staff_weekly_intervals"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    business_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    staff_member_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("staff_members.id", ondelete="RESTRICT"), nullable=False
    )
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    kind: Mapped[ScheduleIntervalKind] = mapped_column(
        database_enum(ScheduleIntervalKind, name="schedule_interval_kind"), nullable=False
    )
    start_minute: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    end_minute: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_by_staff_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("staff_members.id", ondelete="RESTRICT"), nullable=False
    )

    __table_args__ = (
        CheckConstraint("weekday BETWEEN 0 AND 6", name="weekday_valid"),
        CheckConstraint(
            "start_minute >= 0 AND start_minute < end_minute AND end_minute <= 1440",
            name="minute_range_valid",
        ),
        ExcludeConstraint(
            (business_id, "="),
            (staff_member_id, "="),
            (weekday, "="),
            (kind, "="),
            (func.int4range(start_minute, end_minute, "[)"), "&&"),
            where=text("is_active"),
            using="gist",
            name="ex_staff_weekly_intervals_overlap",
        ),
        Index(
            "ix_staff_weekly_intervals_projection",
            "business_id",
            "staff_member_id",
            "weekday",
            "is_active",
        ),
    )


class StaffScheduleException(TimestampMixin, Base):
    """A local-date closure, custom working window, or one-off break."""

    __tablename__ = "staff_schedule_exceptions"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    business_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    staff_member_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("staff_members.id", ondelete="RESTRICT"), nullable=False
    )
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    kind: Mapped[ScheduleExceptionKind] = mapped_column(
        database_enum(ScheduleExceptionKind, name="schedule_exception_kind"), nullable=False
    )
    start_minute: Mapped[int | None] = mapped_column(SmallInteger)
    end_minute: Mapped[int | None] = mapped_column(SmallInteger)
    reason: Mapped[str | None] = mapped_column(String(500))
    private_note: Mapped[str | None] = mapped_column(Text)
    created_by_staff_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("staff_members.id", ondelete="RESTRICT"), nullable=False
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "((kind IN ('working_window', 'break')) "
            "AND start_minute IS NOT NULL AND end_minute IS NOT NULL "
            "AND start_minute >= 0 AND start_minute < end_minute AND end_minute <= 1440) "
            "OR ((kind IN ('day_off', 'leave', 'sick')) "
            "AND start_minute IS NULL AND end_minute IS NULL)",
            name="kind_time_shape_valid",
        ),
        CheckConstraint(
            "private_note IS NULL OR char_length(private_note) <= 2000",
            name="private_note_length_valid",
        ),
        ExcludeConstraint(
            (business_id, "="),
            (staff_member_id, "="),
            (local_date, "="),
            (kind, "="),
            (func.int4range(start_minute, end_minute, "[)"), "&&"),
            where=text("archived_at IS NULL AND kind IN ('working_window', 'break')"),
            using="gist",
            name="ex_staff_schedule_exceptions_overlap",
        ),
        Index(
            "uq_staff_schedule_exception_all_day",
            "business_id",
            "staff_member_id",
            "local_date",
            unique=True,
            postgresql_where=text("archived_at IS NULL AND kind IN ('day_off', 'leave', 'sick')"),
        ),
        Index(
            "ix_staff_schedule_exceptions_projection",
            "business_id",
            "staff_member_id",
            "local_date",
            "archived_at",
        ),
    )
