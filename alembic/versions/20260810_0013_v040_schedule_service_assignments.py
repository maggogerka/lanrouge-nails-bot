"""Add lazy multi-master schedules and per-master service assignments.

Revision ID: 20260810_0013
Revises: 20260810_0012
Create Date: 2026-08-10 14:00:00+03:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260810_0013"
down_revision: str | None = "20260810_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

schedule_interval_kind = postgresql.ENUM(
    "work", "break", name="schedule_interval_kind", create_type=False
)
schedule_exception_kind = postgresql.ENUM(
    "day_off",
    "leave",
    "sick",
    "working_window",
    "break",
    name="schedule_exception_kind",
    create_type=False,
)


def _timestamps() -> tuple[sa.Column[object], sa.Column[object]]:
    return (
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
    )


def upgrade() -> None:
    bind = op.get_bind()
    schedule_interval_kind.create(bind, checkfirst=True)
    schedule_exception_kind.create(bind, checkfirst=True)

    op.create_table(
        "service_categories",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("business_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.CheckConstraint(
            "char_length(name) BETWEEN 1 AND 100",
            name=op.f("ck_service_categories_name_length_valid"),
        ),
        sa.CheckConstraint(
            "sort_order BETWEEN -100000 AND 100000",
            name=op.f("ck_service_categories_sort_order_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            ondelete="RESTRICT",
            name=op.f("fk_service_categories_business_id_businesses"),
        ),
    )
    op.create_index(
        "uq_service_categories_business_name_ci",
        "service_categories",
        ["business_id", sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )
    op.create_index(
        "ix_service_categories_business_order",
        "service_categories",
        ["business_id", "is_active", "sort_order", "id"],
    )
    op.execute(
        "INSERT INTO service_categories (id, business_id, name, sort_order, is_active) "
        "VALUES (1, 1, 'Основные услуги', 0, true)"
    )
    op.execute(
        "SELECT setval(pg_get_serial_sequence('service_categories', 'id'), 1, true)"
    )

    op.add_column("services", sa.Column("category_id", sa.BigInteger()))
    op.create_foreign_key(
        op.f("fk_services_category_id_service_categories"),
        "services",
        "service_categories",
        ["category_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.execute("UPDATE services SET category_id = 1 WHERE category_id IS NULL")

    op.create_table(
        "staff_service_assignments",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("business_id", sa.BigInteger(), nullable=False),
        sa.Column("staff_member_id", sa.BigInteger(), nullable=False),
        sa.Column("service_id", sa.BigInteger(), nullable=False),
        sa.Column("price_override", sa.Numeric(12, 2)),
        sa.Column("duration_min_minutes_override", sa.Integer()),
        sa.Column("duration_max_minutes_override", sa.Integer()),
        sa.Column("prepayment_amount_override", sa.Numeric(12, 2)),
        sa.Column("prepayment_percent_override", sa.Numeric(5, 2)),
        sa.Column(
            "online_booking_enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.CheckConstraint(
            "price_override IS NULL OR price_override >= 0",
            name=op.f("ck_staff_service_assignments_price_valid"),
        ),
        sa.CheckConstraint(
            "(duration_min_minutes_override IS NULL AND duration_max_minutes_override IS NULL) "
            "OR (duration_min_minutes_override > 0 AND duration_max_minutes_override > 0 "
            "AND duration_min_minutes_override <= duration_max_minutes_override)",
            name=op.f("ck_staff_service_assignments_duration_override_valid"),
        ),
        sa.CheckConstraint(
            "prepayment_amount_override IS NULL OR prepayment_amount_override >= 0",
            name=op.f("ck_staff_service_assignments_prepayment_amount_valid"),
        ),
        sa.CheckConstraint(
            "prepayment_percent_override IS NULL OR prepayment_percent_override BETWEEN 0 AND 100",
            name=op.f("ck_staff_service_assignments_prepayment_percent_valid"),
        ),
        sa.CheckConstraint(
            "prepayment_amount_override IS NULL OR prepayment_percent_override IS NULL",
            name=op.f("ck_staff_service_assignments_single_prepayment_kind"),
        ),
        sa.CheckConstraint(
            "sort_order BETWEEN -100000 AND 100000",
            name=op.f("ck_staff_service_assignments_sort_order_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            ondelete="RESTRICT",
            name=op.f("fk_staff_service_assignments_business_id_businesses"),
        ),
        sa.ForeignKeyConstraint(
            ["staff_member_id"],
            ["staff_members.id"],
            ondelete="RESTRICT",
            name=op.f("fk_staff_service_assignments_staff_member_id_staff_members"),
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            ondelete="RESTRICT",
            name=op.f("fk_staff_service_assignments_service_id_services"),
        ),
    )
    op.create_index(
        "uq_staff_service_assignments_business_staff_service",
        "staff_service_assignments",
        ["business_id", "staff_member_id", "service_id"],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )
    op.create_index(
        "ix_staff_service_assignments_bookable",
        "staff_service_assignments",
        [
            "business_id",
            "service_id",
            "is_active",
            "online_booking_enabled",
            "staff_member_id",
        ],
        postgresql_where=sa.text("archived_at IS NULL"),
    )
    op.execute(
        """
        INSERT INTO staff_service_assignments (
            id, business_id, staff_member_id, service_id, is_active,
            online_booking_enabled, sort_order
        )
        SELECT service.id, service.business_id, 1, service.id, true,
               service.online_booking_enabled, service.sort_order
        FROM services AS service
        """
    )
    op.execute(
        "SELECT setval(pg_get_serial_sequence('staff_service_assignments', 'id'), "
        "GREATEST(COALESCE((SELECT max(id) FROM staff_service_assignments), 1), 1), true)"
    )

    op.create_table(
        "staff_weekly_intervals",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("business_id", sa.BigInteger(), nullable=False),
        sa.Column("staff_member_id", sa.BigInteger(), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("kind", schedule_interval_kind, nullable=False),
        sa.Column("start_minute", sa.SmallInteger(), nullable=False),
        sa.Column("end_minute", sa.SmallInteger(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_by_staff_id", sa.BigInteger(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "weekday BETWEEN 0 AND 6",
            name=op.f("ck_staff_weekly_intervals_weekday_valid"),
        ),
        sa.CheckConstraint(
            "start_minute >= 0 AND start_minute < end_minute AND end_minute <= 1440",
            name=op.f("ck_staff_weekly_intervals_minute_range_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"], ondelete="RESTRICT", name=op.f("fk_staff_weekly_intervals_business_id_businesses")
        ),
        sa.ForeignKeyConstraint(
            ["staff_member_id"], ["staff_members.id"], ondelete="RESTRICT", name=op.f("fk_staff_weekly_intervals_staff_member_id_staff_members")
        ),
        sa.ForeignKeyConstraint(
            ["created_by_staff_id"], ["staff_members.id"], ondelete="RESTRICT", name=op.f("fk_staff_weekly_intervals_created_by_staff_id_staff_members")
        ),
    )
    op.create_index(
        "ix_staff_weekly_intervals_projection",
        "staff_weekly_intervals",
        ["business_id", "staff_member_id", "weekday", "is_active"],
    )
    op.execute(
        """
        ALTER TABLE staff_weekly_intervals
        ADD CONSTRAINT ex_staff_weekly_intervals_overlap
        EXCLUDE USING gist (
            business_id WITH =, staff_member_id WITH =, weekday WITH =, kind WITH =,
            int4range(start_minute, end_minute, '[)') WITH &&
        ) WHERE (is_active)
        """
    )

    op.create_table(
        "staff_schedule_exceptions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("business_id", sa.BigInteger(), nullable=False),
        sa.Column("staff_member_id", sa.BigInteger(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("kind", schedule_exception_kind, nullable=False),
        sa.Column("start_minute", sa.SmallInteger()),
        sa.Column("end_minute", sa.SmallInteger()),
        sa.Column("reason", sa.String(length=500)),
        sa.Column("private_note", sa.Text()),
        sa.Column("created_by_staff_id", sa.BigInteger(), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.CheckConstraint(
            "((kind IN ('working_window', 'break')) AND start_minute IS NOT NULL "
            "AND end_minute IS NOT NULL AND start_minute >= 0 AND start_minute < end_minute "
            "AND end_minute <= 1440) OR ((kind IN ('day_off', 'leave', 'sick')) "
            "AND start_minute IS NULL AND end_minute IS NULL)",
            name=op.f("ck_staff_schedule_exceptions_kind_time_shape_valid"),
        ),
        sa.CheckConstraint(
            "private_note IS NULL OR char_length(private_note) <= 2000",
            name=op.f("ck_staff_schedule_exceptions_private_note_length_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"], ondelete="RESTRICT", name=op.f("fk_staff_schedule_exceptions_business_id_businesses")
        ),
        sa.ForeignKeyConstraint(
            ["staff_member_id"], ["staff_members.id"], ondelete="RESTRICT", name=op.f("fk_staff_schedule_exceptions_staff_member_id_staff_members")
        ),
        sa.ForeignKeyConstraint(
            ["created_by_staff_id"], ["staff_members.id"], ondelete="RESTRICT", name=op.f("fk_staff_schedule_exceptions_created_by_staff_id_staff_members")
        ),
    )
    op.create_index(
        "uq_staff_schedule_exception_all_day",
        "staff_schedule_exceptions",
        ["business_id", "staff_member_id", "local_date"],
        unique=True,
        postgresql_where=sa.text(
            "archived_at IS NULL AND kind IN ('day_off', 'leave', 'sick')"
        ),
    )
    op.create_index(
        "ix_staff_schedule_exceptions_projection",
        "staff_schedule_exceptions",
        ["business_id", "staff_member_id", "local_date", "archived_at"],
    )
    op.execute(
        """
        ALTER TABLE staff_schedule_exceptions
        ADD CONSTRAINT ex_staff_schedule_exceptions_overlap
        EXCLUDE USING gist (
            business_id WITH =, staff_member_id WITH =, local_date WITH =, kind WITH =,
            int4range(start_minute, end_minute, '[)') WITH &&
        ) WHERE (archived_at IS NULL AND kind IN ('working_window', 'break'))
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "Downgrading v0.4 schedules can discard staff data; restore the v0.3.1 backup"
    )
