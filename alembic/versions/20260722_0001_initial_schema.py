"""Create the v0.1.0 core schema and initial business settings.

Revision ID: 20260722_0001
Revises:
Create Date: 2026-07-22 17:30:00+03:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260722_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

user_role = postgresql.ENUM("client", "admin", name="user_role", create_type=False)
window_status = postgresql.ENUM(
    "open",
    "reserved",
    "booked",
    "closed",
    "expired",
    name="availability_window_status",
    create_type=False,
)
appointment_status = postgresql.ENUM(
    "confirmed",
    "client_confirmed",
    "completed",
    "cancelled_by_client",
    "cancelled_by_admin",
    "no_show",
    "rescheduled",
    name="appointment_status",
    create_type=False,
)
notification_job_status = postgresql.ENUM(
    "pending",
    "processing",
    "sent",
    "failed",
    "cancelled",
    name="notification_job_status",
    create_type=False,
)
notification_type = postgresql.ENUM(
    "client_reminder",
    "admin_reminder",
    name="notification_type",
    create_type=False,
)


def upgrade() -> None:
    """Create enums, tables, indexes, constraints and the singleton settings row."""

    bind = op.get_bind()
    user_role.create(bind, checkfirst=True)
    window_status.create(bind, checkfirst=True)
    appointment_status.create(bind, checkfirst=True)
    notification_job_status.create(bind, checkfirst=True)
    notification_type.create(bind, checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("telegram_id", sa.BigInteger(), nullable=False),
        sa.Column("username", sa.String(length=64)),
        sa.Column("first_name", sa.String(length=255)),
        sa.Column("last_name", sa.String(length=255)),
        sa.Column("phone", sa.String(length=32)),
        sa.Column("role", user_role, server_default="client", nullable=False),
        sa.Column("privacy_consent_at", sa.DateTime(timezone=True)),
        sa.Column("marketing_consent_at", sa.DateTime(timezone=True)),
        sa.Column("marketing_unsubscribed_at", sa.DateTime(timezone=True)),
        sa.Column("is_blocked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("telegram_id", name="uq_users_telegram_id"),
    )
    op.create_index("ix_users_marketing_consent", "users", ["marketing_consent_at"])

    op.create_table(
        "services",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("price", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("duration_min_minutes", sa.Integer(), nullable=False),
        sa.Column("duration_max_minutes", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("price >= 0", name=op.f("ck_services_price_non_negative")),
        sa.CheckConstraint(
            "duration_min_minutes > 0",
            name=op.f("ck_services_duration_min_positive"),
        ),
        sa.CheckConstraint(
            "duration_max_minutes > 0",
            name=op.f("ck_services_duration_max_positive"),
        ),
        sa.CheckConstraint(
            "duration_min_minutes <= duration_max_minutes",
            name=op.f("ck_services_duration_range_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_services"),
    )
    op.create_index("ix_services_active_name", "services", ["is_active", "name"])

    op.create_table(
        "availability_windows",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", window_status, server_default="open", nullable=False),
        sa.Column("admin_comment", sa.Text()),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "start_at < end_at",
            name=op.f("ck_availability_windows_positive_duration"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_availability_windows_created_by_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_availability_windows"),
    )
    op.create_index(
        "ix_availability_windows_status_start",
        "availability_windows",
        ["status", "start_at"],
    )
    op.create_index("ix_availability_windows_start", "availability_windows", ["start_at"])
    op.execute(
        """
        ALTER TABLE availability_windows
        ADD CONSTRAINT ex_availability_windows_active_overlap
        EXCLUDE USING gist (
            tstzrange(start_at, end_at, '[)') WITH &&
        )
        WHERE (status IN ('open', 'reserved', 'booked'))
        """
    )

    op.create_table(
        "appointments",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("client_id", sa.BigInteger(), nullable=False),
        sa.Column("window_id", sa.BigInteger(), nullable=False),
        sa.Column("service_id", sa.BigInteger(), nullable=False),
        sa.Column("rescheduled_from_id", sa.BigInteger()),
        sa.Column("service_name_snapshot", sa.String(length=255), nullable=False),
        sa.Column("price_snapshot", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("duration_min_snapshot", sa.Integer(), nullable=False),
        sa.Column("duration_max_snapshot", sa.Integer(), nullable=False),
        sa.Column("status", appointment_status, server_default="confirmed", nullable=False),
        sa.Column("client_comment", sa.Text()),
        sa.Column("client_confirmed_at", sa.DateTime(timezone=True)),
        sa.Column("cancelled_at", sa.DateTime(timezone=True)),
        sa.Column("cancellation_reason", sa.Text()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "price_snapshot >= 0",
            name=op.f("ck_appointments_price_snapshot_non_negative"),
        ),
        sa.CheckConstraint(
            "duration_min_snapshot > 0",
            name=op.f("ck_appointments_duration_min_snapshot_positive"),
        ),
        sa.CheckConstraint(
            "duration_max_snapshot > 0",
            name=op.f("ck_appointments_duration_max_snapshot_positive"),
        ),
        sa.CheckConstraint(
            "duration_min_snapshot <= duration_max_snapshot",
            name=op.f("ck_appointments_duration_snapshot_range_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["client_id"],
            ["users.id"],
            name="fk_appointments_client_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["window_id"],
            ["availability_windows.id"],
            name="fk_appointments_window_id_availability_windows",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name="fk_appointments_service_id_services",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rescheduled_from_id"],
            ["appointments.id"],
            name="fk_appointments_rescheduled_from_id_appointments",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_appointments"),
        sa.UniqueConstraint(
            "rescheduled_from_id",
            name="uq_appointments_rescheduled_from_id",
        ),
    )
    op.create_index(
        "ix_appointments_client_status",
        "appointments",
        ["client_id", "status"],
    )
    op.create_index("ix_appointments_window", "appointments", ["window_id"])
    op.create_index(
        "uq_appointments_occupied_window",
        "appointments",
        ["window_id"],
        unique=True,
        postgresql_where=sa.text(
            "status NOT IN ('cancelled_by_client', 'cancelled_by_admin', 'rescheduled')"
        ),
    )

    op.create_table(
        "appointment_status_history",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("appointment_id", sa.BigInteger(), nullable=False),
        sa.Column("previous_status", appointment_status),
        sa.Column("new_status", appointment_status, nullable=False),
        sa.Column("changed_by_user_id", sa.BigInteger()),
        sa.Column("reason", sa.String(length=500)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["appointments.id"],
            name="fk_appointment_status_history_appointment_id_appointments",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["changed_by_user_id"],
            ["users.id"],
            name="fk_appointment_status_history_changed_by_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_appointment_status_history"),
    )
    op.create_index(
        "ix_appointment_status_history_appointment",
        "appointment_status_history",
        ["appointment_id"],
    )

    op.create_table(
        "notification_jobs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("appointment_id", sa.BigInteger(), nullable=False),
        sa.Column("recipient_user_id", sa.BigInteger(), nullable=False),
        sa.Column("notification_type", notification_type, nullable=False),
        sa.Column("offset_minutes", sa.Integer(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            notification_job_status,
            server_default="pending",
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("locked_by", sa.String(length=128)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(length=1000)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "offset_minutes > 0",
            name=op.f("ck_notification_jobs_offset_positive"),
        ),
        sa.CheckConstraint(
            "attempts >= 0",
            name=op.f("ck_notification_jobs_attempts_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["appointment_id"],
            ["appointments.id"],
            name="fk_notification_jobs_appointment_id_appointments",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recipient_user_id"],
            ["users.id"],
            name="fk_notification_jobs_recipient_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_notification_jobs"),
        sa.UniqueConstraint(
            "appointment_id",
            "recipient_user_id",
            "notification_type",
            "offset_minutes",
            name="uq_notification_jobs_delivery",
        ),
    )
    op.create_index(
        "ix_notification_jobs_due",
        "notification_jobs",
        ["status", "available_at"],
    )
    op.create_index(
        "ix_notification_jobs_appointment",
        "notification_jobs",
        ["appointment_id"],
    )

    op.create_table(
        "business_settings",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("business_name", sa.String(length=255), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("address", sa.String(length=500), nullable=False),
        sa.Column("map_url", sa.String(length=2048), nullable=False),
        sa.Column("master_telegram_url", sa.String(length=2048), nullable=False),
        sa.Column("booking_horizon_days", sa.Integer(), nullable=False),
        sa.Column("cancellation_deadline_hours", sa.Integer(), nullable=False),
        sa.Column("max_appointments_per_day", sa.Integer(), nullable=False),
        sa.Column("default_window_duration_minutes", sa.Integer(), nullable=False),
        sa.Column("minimum_gap_minutes", sa.Integer(), nullable=False),
        sa.Column("allow_saturday", sa.Boolean(), nullable=False),
        sa.Column("allow_sunday", sa.Boolean(), nullable=False),
        sa.Column("reminder_offsets_minutes", postgresql.ARRAY(sa.Integer()), nullable=False),
        sa.Column("version", sa.BigInteger(), server_default="1", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("id = 1", name=op.f("ck_business_settings_singleton")),
        sa.CheckConstraint(
            "booking_horizon_days > 0",
            name=op.f("ck_business_settings_booking_horizon_positive"),
        ),
        sa.CheckConstraint(
            "cancellation_deadline_hours > 0",
            name=op.f("ck_business_settings_cancellation_deadline_positive"),
        ),
        sa.CheckConstraint(
            "max_appointments_per_day > 0",
            name=op.f("ck_business_settings_max_appointments_per_day_positive"),
        ),
        sa.CheckConstraint(
            "default_window_duration_minutes > 0",
            name=op.f("ck_business_settings_default_window_duration_positive"),
        ),
        sa.CheckConstraint(
            "minimum_gap_minutes >= 0",
            name=op.f("ck_business_settings_minimum_gap_non_negative"),
        ),
        sa.CheckConstraint(
            "version > 0",
            name=op.f("ck_business_settings_version_positive"),
        ),
        sa.PrimaryKeyConstraint("id", name="pk_business_settings"),
    )
    op.execute(
        """
        INSERT INTO business_settings (
            id,
            business_name,
            timezone,
            address,
            map_url,
            master_telegram_url,
            booking_horizon_days,
            cancellation_deadline_hours,
            max_appointments_per_day,
            default_window_duration_minutes,
            minimum_gap_minutes,
            allow_saturday,
            allow_sunday,
            reminder_offsets_minutes,
            version
        ) VALUES (
            1,
            'Бизнес',
            'Europe/Moscow',
            'Новоостаповская, д. 20',
            'https://yandex.ru/maps/-/CTbJz23i',
            'https://t.me/example_service_bot',
            31,
            36,
            2,
            210,
            60,
            false,
            false,
            ARRAY[1440, 180, 60],
            1
        )
        """
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("actor_user_id", sa.BigInteger()),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("entity_type", sa.String(length=100), nullable=False),
        sa.Column("entity_id", sa.String(length=64), nullable=False),
        sa.Column(
            "changes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("correlation_id", sa.String(length=64)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name="fk_audit_logs_actor_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audit_logs"),
    )
    op.create_index("ix_audit_logs_entity", "audit_logs", ["entity_type", "entity_id"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])


def downgrade() -> None:
    """Drop the v0.1.0 schema in reverse dependency order."""

    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_index("ix_audit_logs_entity", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_table("business_settings")
    op.drop_index("ix_notification_jobs_appointment", table_name="notification_jobs")
    op.drop_index("ix_notification_jobs_due", table_name="notification_jobs")
    op.drop_table("notification_jobs")
    op.drop_index(
        "ix_appointment_status_history_appointment",
        table_name="appointment_status_history",
    )
    op.drop_table("appointment_status_history")
    op.drop_index("uq_appointments_occupied_window", table_name="appointments")
    op.drop_index("ix_appointments_window", table_name="appointments")
    op.drop_index("ix_appointments_client_status", table_name="appointments")
    op.drop_table("appointments")
    op.drop_index("ix_availability_windows_start", table_name="availability_windows")
    op.drop_index(
        "ix_availability_windows_status_start",
        table_name="availability_windows",
    )
    op.drop_table("availability_windows")
    op.drop_index("ix_services_active_name", table_name="services")
    op.drop_table("services")
    op.drop_index("ix_users_marketing_consent", table_name="users")
    op.drop_table("users")

    bind = op.get_bind()
    notification_type.drop(bind, checkfirst=True)
    notification_job_status.drop(bind, checkfirst=True)
    appointment_status.drop(bind, checkfirst=True)
    window_status.drop(bind, checkfirst=True)
    user_role.drop(bind, checkfirst=True)
