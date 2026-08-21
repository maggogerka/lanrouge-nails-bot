"""Add isolated short-lived public demo workspaces.

Revision ID: 20260821_0028
Revises: 20260814_0027
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260821_0028"
down_revision: str | None = "20260814_0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "demo_sessions",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("generation", sa.Integer(), server_default="1", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_reset_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("generation > 0", name="ck_demo_sessions_positive_generation"),
        sa.PrimaryKeyConstraint("id", name="pk_demo_sessions"),
        sa.UniqueConstraint("telegram_user_id", name="uq_demo_sessions_telegram_user_id"),
    )
    op.create_index("ix_demo_sessions_expiry", "demo_sessions", ["expires_at"])

    op.create_table(
        "demo_services",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(12, 2), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("duration_minutes BETWEEN 15 AND 480", name="ck_demo_services_duration_range"),
        sa.CheckConstraint("price >= 0", name="ck_demo_services_non_negative_price"),
        sa.ForeignKeyConstraint(["session_id"], ["demo_sessions.id"], name="fk_demo_services_session_id_demo_sessions", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_demo_services"),
        sa.UniqueConstraint("session_id", "name", name="uq_demo_services_session_name"),
    )
    op.create_index("ix_demo_services_session_id", "demo_services", ["session_id"])

    op.create_table(
        "demo_staff",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["demo_sessions.id"], name="fk_demo_staff_session_id_demo_sessions", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_demo_staff"),
        sa.UniqueConstraint("session_id", "name", name="uq_demo_staff_session_name"),
    )
    op.create_index("ix_demo_staff_session_id", "demo_staff", ["session_id"])

    op.create_table(
        "demo_clients",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["session_id"], ["demo_sessions.id"], name="fk_demo_clients_session_id_demo_sessions", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_demo_clients"),
        sa.UniqueConstraint("session_id", "display_name", name="uq_demo_clients_session_name"),
    )
    op.create_index("ix_demo_clients_session_id", "demo_clients", ["session_id"])

    op.create_table(
        "demo_slots",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("staff_id", sa.BigInteger(), nullable=False),
        sa.Column("service_id", sa.BigInteger(), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_available", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("end_at > start_at", name="ck_demo_slots_positive_interval"),
        sa.ForeignKeyConstraint(["session_id"], ["demo_sessions.id"], name="fk_demo_slots_session_id_demo_sessions", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["staff_id"], ["demo_staff.id"], name="fk_demo_slots_staff_id_demo_staff", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["service_id"], ["demo_services.id"], name="fk_demo_slots_service_id_demo_services", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_demo_slots"),
        sa.UniqueConstraint("session_id", "staff_id", "start_at", name="uq_demo_slots_staff_start"),
    )
    op.create_index("ix_demo_slots_session_available_start", "demo_slots", ["session_id", "is_available", "start_at"])

    op.create_table(
        "demo_appointments",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_id", sa.BigInteger(), nullable=False),
        sa.Column("client_id", sa.BigInteger(), nullable=False),
        sa.Column("service_id", sa.BigInteger(), nullable=False),
        sa.Column("staff_id", sa.BigInteger(), nullable=False),
        sa.Column("slot_id", sa.BigInteger(), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="confirmed", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("end_at > start_at", name="ck_demo_appointments_positive_interval"),
        sa.ForeignKeyConstraint(["client_id"], ["demo_clients.id"], name="fk_demo_appointments_client_id_demo_clients", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["service_id"], ["demo_services.id"], name="fk_demo_appointments_service_id_demo_services", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["session_id"], ["demo_sessions.id"], name="fk_demo_appointments_session_id_demo_sessions", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["slot_id"], ["demo_slots.id"], name="fk_demo_appointments_slot_id_demo_slots", ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["staff_id"], ["demo_staff.id"], name="fk_demo_appointments_staff_id_demo_staff", ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_demo_appointments"),
        sa.UniqueConstraint("slot_id", name="uq_demo_appointments_slot"),
    )
    op.create_index("ix_demo_appointments_session_start", "demo_appointments", ["session_id", "start_at"])


def downgrade() -> None:
    op.drop_table("demo_appointments")
    op.drop_table("demo_slots")
    op.drop_table("demo_clients")
    op.drop_table("demo_staff")
    op.drop_table("demo_services")
    op.drop_table("demo_sessions")
