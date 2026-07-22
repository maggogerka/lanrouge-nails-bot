"""Add broadcast campaigns, frozen recipients and marketing events.

Revision ID: 20260722_0005
Revises: 20260722_0004
Create Date: 2026-07-22 22:15:00+03:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260722_0005"
down_revision: str | None = "20260722_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

broadcast_status = postgresql.ENUM(
    "draft",
    "scheduled",
    "preparing",
    "sending",
    "completed",
    "partially_failed",
    "cancelled",
    "failed",
    name="broadcast_status",
    create_type=False,
)
broadcast_recipient_status = postgresql.ENUM(
    "pending",
    "processing",
    "sent",
    "retry",
    "failed",
    "skipped",
    "unsubscribed",
    "blocked",
    name="broadcast_recipient_status",
    create_type=False,
)
broadcast_audience_type = postgresql.ENUM(
    "all_subscribed",
    "client_tag",
    "service_history",
    "inactive_days",
    "manual",
    name="broadcast_audience_type",
    create_type=False,
)
broadcast_button_type = postgresql.ENUM(
    "none",
    "book",
    "portfolio",
    "available_windows",
    "url",
    name="broadcast_button_type",
    create_type=False,
)
marketing_event_type = postgresql.ENUM(
    "booking_clicked",
    "portfolio_clicked",
    "windows_clicked",
    name="marketing_event_type",
    create_type=False,
)
media_type = postgresql.ENUM("photo", name="media_type", create_type=False)


def upgrade() -> None:
    """Create campaigns separately from appointment notification jobs."""

    bind = op.get_bind()
    broadcast_status.create(bind, checkfirst=True)
    broadcast_recipient_status.create(bind, checkfirst=True)
    broadcast_audience_type.create(bind, checkfirst=True)
    broadcast_button_type.create(bind, checkfirst=True)
    marketing_event_type.create(bind, checkfirst=True)

    op.create_table(
        "broadcasts",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("parse_mode", sa.String(length=16)),
        sa.Column("status", broadcast_status, server_default="draft", nullable=False),
        sa.Column("audience_type", broadcast_audience_type, nullable=False),
        sa.Column(
            "audience_parameters",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "button_type", broadcast_button_type, server_default="none", nullable=False
        ),
        sa.Column("button_text", sa.String(length=100)),
        sa.Column("button_url", sa.String(length=2048)),
        sa.Column("linked_portfolio_item_id", sa.BigInteger()),
        sa.Column("scheduled_at", sa.DateTime(timezone=True)),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
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
        sa.ForeignKeyConstraint(
            ["linked_portfolio_item_id"],
            ["portfolio_items.id"],
            name="fk_broadcasts_linked_portfolio_item_id_portfolio_items",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name="fk_broadcasts_created_by_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_broadcasts"),
    )
    op.create_index(
        "ix_broadcasts_status_schedule", "broadcasts", ["status", "scheduled_at"]
    )

    op.create_table(
        "broadcast_media",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("broadcast_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_file_id", sa.String(length=512), nullable=False),
        sa.Column("telegram_file_unique_id", sa.String(length=255), nullable=False),
        sa.Column("media_type", media_type, server_default="photo", nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("position >= 0", name="ck_broadcast_media_position_non_negative"),
        sa.ForeignKeyConstraint(
            ["broadcast_id"],
            ["broadcasts.id"],
            name="fk_broadcast_media_broadcast_id_broadcasts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_broadcast_media"),
        sa.UniqueConstraint("broadcast_id", "position", name="uq_broadcast_media_position"),
        sa.UniqueConstraint(
            "broadcast_id", "telegram_file_unique_id", name="uq_broadcast_media_file"
        ),
    )

    op.create_table(
        "broadcast_recipients",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("broadcast_id", sa.BigInteger(), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "status", broadcast_recipient_status, server_default="pending", nullable=False
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True)),
        sa.Column("locked_by", sa.String(length=128)),
        sa.Column("sent_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.String(length=1000)),
        sa.Column("telegram_message_id", sa.BigInteger()),
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
            "attempts >= 0", name="ck_broadcast_recipients_attempts_non_negative"
        ),
        sa.ForeignKeyConstraint(
            ["broadcast_id"],
            ["broadcasts.id"],
            name="fk_broadcast_recipients_broadcast_id_broadcasts",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_broadcast_recipients_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_broadcast_recipients"),
        sa.UniqueConstraint("broadcast_id", "user_id", name="uq_broadcast_recipients_user"),
    )
    op.create_index(
        "ix_broadcast_recipients_due",
        "broadcast_recipients",
        ["status", "available_at"],
    )
    op.create_index(
        "ix_broadcast_recipients_broadcast_status",
        "broadcast_recipients",
        ["broadcast_id", "status"],
    )

    op.create_table(
        "marketing_events",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("broadcast_id", sa.BigInteger(), nullable=False),
        sa.Column("event_type", marketing_event_type, nullable=False),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_marketing_events_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["broadcast_id"],
            ["broadcasts.id"],
            name="fk_marketing_events_broadcast_id_broadcasts",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_marketing_events"),
    )
    op.create_index(
        "ix_marketing_events_broadcast_created",
        "marketing_events",
        ["broadcast_id", "created_at"],
    )


def downgrade() -> None:
    """Drop campaigns after stopping the broadcast worker."""

    op.drop_index("ix_marketing_events_broadcast_created", table_name="marketing_events")
    op.drop_table("marketing_events")
    op.drop_index(
        "ix_broadcast_recipients_broadcast_status", table_name="broadcast_recipients"
    )
    op.drop_index("ix_broadcast_recipients_due", table_name="broadcast_recipients")
    op.drop_table("broadcast_recipients")
    op.drop_table("broadcast_media")
    op.drop_index("ix_broadcasts_status_schedule", table_name="broadcasts")
    op.drop_table("broadcasts")

    bind = op.get_bind()
    marketing_event_type.drop(bind, checkfirst=True)
    broadcast_button_type.drop(bind, checkfirst=True)
    broadcast_audience_type.drop(bind, checkfirst=True)
    broadcast_recipient_status.drop(bind, checkfirst=True)
    broadcast_status.drop(bind, checkfirst=True)
