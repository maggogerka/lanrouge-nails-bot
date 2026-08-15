"""Make privacy anonymization bounded, recoverable and concurrency safe.

Revision ID: 20260811_0020
Revises: 20260811_0019
Create Date: 2026-08-11 18:00:00+03:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260811_0020"
down_revision: str | None = "20260811_0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE data_deletion_request_status ADD VALUE IF NOT EXISTS 'processing'")
        op.execute("ALTER TYPE data_deletion_request_status ADD VALUE IF NOT EXISTS 'failed'")

    op.add_column(
        "data_deletion_requests",
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column(
        "data_deletion_requests",
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
    )
    op.add_column("data_deletion_requests", sa.Column("locked_at", sa.DateTime(timezone=True)))
    op.add_column("data_deletion_requests", sa.Column("locked_by", sa.String(length=64)))
    op.add_column("data_deletion_requests", sa.Column("last_error_code", sa.String(length=100)))
    op.create_check_constraint(
        op.f("ck_data_deletion_requests_deletion_attempts_valid"),
        "data_deletion_requests",
        "attempt_count >= 0 AND max_attempts BETWEEN 1 AND 10 AND attempt_count <= max_attempts",
    )
    op.create_check_constraint(
        op.f("ck_data_deletion_requests_deletion_processing_lock_valid"),
        "data_deletion_requests",
        "(status = 'processing') = (locked_at IS NOT NULL AND locked_by IS NOT NULL)",
    )
    op.drop_index(
        "uq_data_deletion_requests_open_client",
        table_name="data_deletion_requests",
    )
    op.create_index(
        "uq_data_deletion_requests_open_client",
        "data_deletion_requests",
        ["business_id", "business_client_id"],
        unique=True,
        postgresql_where=sa.text(
            "status IN ('requested', 'in_review', 'approved', 'processing', 'failed')"
        ),
    )


def downgrade() -> None:
    op.execute(
        "UPDATE data_deletion_requests SET status = 'approved', locked_at = NULL, "
        "locked_by = NULL WHERE status IN ('processing', 'failed')"
    )
    op.drop_index(
        "uq_data_deletion_requests_open_client",
        table_name="data_deletion_requests",
    )
    op.create_index(
        "uq_data_deletion_requests_open_client",
        "data_deletion_requests",
        ["business_id", "business_client_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('requested', 'in_review', 'approved')"),
    )
    op.drop_constraint(
        op.f("ck_data_deletion_requests_deletion_processing_lock_valid"),
        "data_deletion_requests",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_data_deletion_requests_deletion_attempts_valid"),
        "data_deletion_requests",
        type_="check",
    )
    op.drop_column("data_deletion_requests", "last_error_code")
    op.drop_column("data_deletion_requests", "locked_by")
    op.drop_column("data_deletion_requests", "locked_at")
    op.drop_column("data_deletion_requests", "max_attempts")
    op.drop_column("data_deletion_requests", "attempt_count")
    # PostgreSQL enum values are intentionally retained; removing them is unsafe.
