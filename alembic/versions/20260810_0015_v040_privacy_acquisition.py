"""Add deletion workflow, versioned privacy records, and acquisition attribution.

Revision ID: 20260810_0015
Revises: 20260810_0014
Create Date: 2026-08-10 16:00:00+03:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260810_0015"
down_revision: str | None = "20260810_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

deletion_status = postgresql.ENUM(
    "requested",
    "in_review",
    "approved",
    "rejected",
    "completed",
    "cancelled",
    name="data_deletion_request_status",
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
    deletion_status.create(op.get_bind(), checkfirst=True)

    # v0.4 scope migration already marked legacy consent as unversioned. Fill
    # the explicit revocation timestamp and harden the proof fields here.
    op.execute(
        "UPDATE consent_history SET revoked_at = created_at "
        "WHERE new_value = false AND revoked_at IS NULL"
    )
    op.create_check_constraint(
        op.f("ck_consent_history_policy_version_length_valid"),
        "consent_history",
        "char_length(policy_version) BETWEEN 1 AND 64",
    )
    op.create_check_constraint(
        op.f("ck_consent_history_policy_hash_format_valid"),
        "consent_history",
        "policy_hash IS NULL OR policy_hash ~ '^[0-9a-f]{64}$'",
    )

    op.create_table(
        "data_deletion_requests",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("business_id", sa.BigInteger(), nullable=False),
        sa.Column("business_client_id", sa.BigInteger(), nullable=False),
        sa.Column("status", deletion_status, server_default="requested", nullable=False),
        sa.Column(
            "request_reason_code",
            sa.String(length=100),
            server_default="client_request",
            nullable=False,
        ),
        sa.Column("correlation_id", sa.String(length=64)),
        sa.Column("requested_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_by_staff_id", sa.BigInteger()),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("retention_reason", sa.Text()),
        sa.Column("result_code", sa.String(length=100)),
        sa.Column(
            "anonymization_plan",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "anonymization_result",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        *_timestamps(),
        sa.CheckConstraint(
            "correlation_id IS NULL OR char_length(correlation_id) BETWEEN 1 AND 64",
            name=op.f("ck_data_deletion_requests_correlation_id_length_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"], ondelete="RESTRICT", name=op.f("fk_data_deletion_requests_business_id_businesses")
        ),
        sa.ForeignKeyConstraint(
            ["business_client_id"], ["business_clients.id"], ondelete="RESTRICT", name=op.f("fk_data_deletion_requests_business_client_id_business_clients")
        ),
        sa.ForeignKeyConstraint(
            ["processed_by_staff_id"], ["staff_members.id"], ondelete="RESTRICT", name=op.f("fk_data_deletion_requests_processed_by_staff_id_staff_members")
        ),
    )
    op.create_index(
        "uq_data_deletion_requests_open_client",
        "data_deletion_requests",
        ["business_id", "business_client_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('requested', 'in_review', 'approved')"),
    )
    op.create_index(
        "ix_data_deletion_requests_business_status_requested",
        "data_deletion_requests",
        ["business_id", "status", "requested_at"],
    )

    op.create_table(
        "data_deletion_request_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("business_id", sa.BigInteger(), nullable=False),
        sa.Column("request_id", sa.BigInteger(), nullable=False),
        sa.Column("previous_status", deletion_status),
        sa.Column("new_status", deletion_status, nullable=False),
        sa.Column("actor_user_id", sa.BigInteger()),
        sa.Column("actor_staff_id", sa.BigInteger()),
        sa.Column(
            "safe_details",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "previous_status IS NOT NULL OR new_status = 'requested'",
            name=op.f("ck_data_deletion_request_events_initial_event_is_requested"),
        ),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"], ondelete="RESTRICT", name=op.f("fk_data_deletion_request_events_business_id_businesses")
        ),
        sa.ForeignKeyConstraint(
            ["request_id"], ["data_deletion_requests.id"], ondelete="RESTRICT", name=op.f("fk_data_deletion_request_events_request_id_data_deletion_requests")
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], ondelete="RESTRICT", name=op.f("fk_data_deletion_request_events_actor_user_id_users")
        ),
        sa.ForeignKeyConstraint(
            ["actor_staff_id"], ["staff_members.id"], ondelete="RESTRICT", name=op.f("fk_data_deletion_request_events_actor_staff_id_staff_members")
        ),
    )
    op.create_index(
        "ix_data_deletion_request_events_request_created",
        "data_deletion_request_events",
        ["request_id", "created_at"],
    )

    op.create_table(
        "acquisition_sources",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("business_id", sa.BigInteger(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("channel", sa.String(length=64)),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("created_by_staff_id", sa.BigInteger()),
        sa.Column("archived_at", sa.DateTime(timezone=True)),
        *_timestamps(),
        sa.CheckConstraint(
            "code ~ '^[a-z0-9][a-z0-9_-]{0,63}$'",
            name=op.f("ck_acquisition_sources_code_format"),
        ),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"], ondelete="RESTRICT", name=op.f("fk_acquisition_sources_business_id_businesses")
        ),
        sa.ForeignKeyConstraint(
            ["created_by_staff_id"], ["staff_members.id"], ondelete="RESTRICT", name=op.f("fk_acquisition_sources_created_by_staff_id_staff_members")
        ),
    )
    op.create_index(
        "uq_acquisition_sources_business_code",
        "acquisition_sources",
        ["business_id", "code"],
        unique=True,
    )
    op.create_index(
        "ix_acquisition_sources_business_active",
        "acquisition_sources",
        ["business_id", "is_active"],
    )
    op.execute(
        """
        INSERT INTO acquisition_sources (business_id, code, display_name, channel)
        VALUES
            (1, 'avito', 'Avito', 'classifieds'),
            (1, 'vk', 'VK', 'social'),
            (1, 'instagram', 'Instagram', 'social'),
            (1, 'qr', 'QR-код', 'offline'),
            (1, 'referral', 'Рекомендация', 'referral')
        ON CONFLICT (business_id, code) DO NOTHING
        """
    )

    op.create_table(
        "client_acquisition_attributions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("business_id", sa.BigInteger(), nullable=False),
        sa.Column("business_client_id", sa.BigInteger(), nullable=False),
        sa.Column("first_source_id", sa.BigInteger(), nullable=False),
        sa.Column("first_touched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_source_id", sa.BigInteger(), nullable=False),
        sa.Column("last_touched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("touch_count", sa.Integer(), server_default="1", nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "touch_count >= 1",
            name=op.f("ck_client_acquisition_attributions_touch_count_positive"),
        ),
        sa.CheckConstraint(
            "last_touched_at >= first_touched_at",
            name=op.f("ck_client_acquisition_attributions_touch_order_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["business_id"], ["businesses.id"], ondelete="RESTRICT", name=op.f("fk_client_acquisition_attributions_business_id_businesses")
        ),
        sa.ForeignKeyConstraint(
            ["business_client_id"], ["business_clients.id"], ondelete="RESTRICT", name=op.f("fk_client_acquisition_attributions_business_client_id_business_clients")
        ),
        sa.ForeignKeyConstraint(
            ["first_source_id"], ["acquisition_sources.id"], ondelete="RESTRICT", name=op.f("fk_client_acquisition_attributions_first_source_id_acquisition_sources")
        ),
        sa.ForeignKeyConstraint(
            ["last_source_id"], ["acquisition_sources.id"], ondelete="RESTRICT", name=op.f("fk_client_acquisition_attributions_last_source_id_acquisition_sources")
        ),
    )
    op.create_index(
        "uq_client_acquisition_attributions_business_client",
        "client_acquisition_attributions",
        ["business_id", "business_client_id"],
        unique=True,
    )
    op.create_index(
        "ix_client_acquisition_attributions_business_last_source",
        "client_acquisition_attributions",
        ["business_id", "last_source_id", "last_touched_at"],
    )

    op.execute(
        """
        INSERT INTO data_deletion_requests (
            business_id, business_client_id, status, request_reason_code,
            correlation_id, requested_at, anonymization_plan, anonymization_result
        )
        SELECT DISTINCT ON (client.id)
            audit.business_id, client.id, 'requested', 'legacy_client_request',
            audit.correlation_id, audit.created_at,
            jsonb_build_object(
                'schema_version', 1,
                'anonymize', jsonb_build_array('user.contact_and_profile'),
                'retain', jsonb_build_array('appointments_and_financial_snapshots')
            ),
            '{}'::jsonb
        FROM audit_logs AS audit
        JOIN business_clients AS client
          ON client.business_id = audit.business_id
         AND audit.entity_id ~ '^[0-9]+$'
         AND client.user_id = audit.entity_id::bigint
        WHERE audit.action = 'privacy.deletion_requested'
        ORDER BY client.id, audit.created_at
        """
    )
    op.execute(
        """
        INSERT INTO data_deletion_request_events (
            business_id, request_id, previous_status, new_status,
            actor_user_id, safe_details, created_at
        )
        SELECT request.business_id, request.id, NULL, 'requested', client.user_id,
               '{"source":"v0.3.1_migration"}'::jsonb, request.requested_at
        FROM data_deletion_requests AS request
        JOIN business_clients AS client ON client.id = request.business_client_id
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "Downgrading privacy workflows can lose legal audit data; restore the v0.3.1 backup"
    )
