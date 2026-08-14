"""Add physical workstations and service-specific availability windows.

Revision ID: 20260814_0026
Revises: 20260814_0025
Create Date: 2026-08-14 05:00:00+03:00
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260814_0026"
down_revision: str | None = "20260814_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workstations",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("business_id", sa.BigInteger(), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name=op.f("fk_workstations_business_id_businesses"),
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "uq_workstations_business_name_ci",
        "workstations",
        ["business_id", sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("archived_at IS NULL"),
    )
    op.create_index(
        "ix_workstations_business_active_order",
        "workstations",
        ["business_id", "is_active", "sort_order", "id"],
    )
    op.create_table(
        "workstation_services",
        sa.Column("workstation_id", sa.BigInteger(), primary_key=True),
        sa.Column("service_id", sa.BigInteger(), primary_key=True),
        sa.Column("business_id", sa.BigInteger(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["business_id"],
            ["businesses.id"],
            name=op.f("fk_workstation_services_business_id_businesses"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["service_id"],
            ["services.id"],
            name=op.f("fk_workstation_services_service_id_services"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["workstation_id"],
            ["workstations.id"],
            name=op.f("fk_workstation_services_workstation_id_workstations"),
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_workstation_services_business_service_active",
        "workstation_services",
        ["business_id", "service_id", "is_active", "workstation_id"],
    )

    op.add_column("availability_windows", sa.Column("service_id", sa.BigInteger()))
    op.add_column("availability_windows", sa.Column("workstation_id", sa.BigInteger()))
    op.create_foreign_key(
        op.f("fk_availability_windows_service_id_services"),
        "availability_windows",
        "services",
        ["service_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_availability_windows_workstation_id_workstations"),
        "availability_windows",
        "workstations",
        ["workstation_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_availability_windows_business_service_start",
        "availability_windows",
        ["business_id", "service_id", "start_at"],
    )
    op.execute(
        """
        ALTER TABLE availability_windows
        ADD CONSTRAINT ex_availability_windows_workstation_active_overlap
        EXCLUDE USING gist (
            workstation_id WITH =,
            tstzrange(start_at, end_at, '[)') WITH &&
        )
        WHERE (
            workstation_id IS NOT NULL
            AND status IN ('open', 'reserved', 'booked')
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE availability_windows DROP CONSTRAINT "
        "ex_availability_windows_workstation_active_overlap"
    )
    op.drop_index(
        "ix_availability_windows_business_service_start",
        table_name="availability_windows",
    )
    op.drop_constraint(
        op.f("fk_availability_windows_workstation_id_workstations"),
        "availability_windows",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_availability_windows_service_id_services"),
        "availability_windows",
        type_="foreignkey",
    )
    op.drop_column("availability_windows", "workstation_id")
    op.drop_column("availability_windows", "service_id")
    op.drop_index(
        "ix_workstation_services_business_service_active",
        table_name="workstation_services",
    )
    op.drop_table("workstation_services")
    op.drop_index("ix_workstations_business_active_order", table_name="workstations")
    op.drop_index("uq_workstations_business_name_ci", table_name="workstations")
    op.drop_table("workstations")
