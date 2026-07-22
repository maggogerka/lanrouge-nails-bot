"""Add review revisions and administrative deletion metadata.

Revision ID: 20260723_0008
Revises: 20260723_0007
Create Date: 2026-07-23 20:00:00+03:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260723_0008"
down_revision: str | None = "20260723_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

review_status = postgresql.ENUM(
    "pending",
    "approved",
    "rejected",
    "hidden",
    name="review_moderation_status",
    create_type=False,
)


def upgrade() -> None:
    """Extend reviews without changing or republishing existing content."""

    op.add_column("reviews", sa.Column("edited_at", sa.DateTime(timezone=True)))
    op.add_column("reviews", sa.Column("edited_by_admin_id", sa.BigInteger()))
    op.add_column(
        "reviews",
        sa.Column(
            "is_admin_edited", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    op.add_column("reviews", sa.Column("deleted_at", sa.DateTime(timezone=True)))
    op.add_column("reviews", sa.Column("deleted_by_user_id", sa.BigInteger()))
    op.add_column("reviews", sa.Column("deletion_reason", sa.Text()))
    op.create_foreign_key(
        "fk_reviews_edited_by_admin_id_users",
        "reviews",
        "users",
        ["edited_by_admin_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        "fk_reviews_deleted_by_user_id_users",
        "reviews",
        "users",
        ["deleted_by_user_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_reviews_deleted_created", "reviews", ["deleted_at", "created_at"])

    op.create_table(
        "review_revisions",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("review_id", sa.BigInteger(), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text()),
        sa.Column("moderation_status", review_status, nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("changed_by_admin_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("rating BETWEEN 1 AND 5", name="ck_review_revisions_rating_valid"),
        sa.CheckConstraint(
            "text IS NULL OR char_length(text) <= 2000",
            name="ck_review_revisions_text_length_valid",
        ),
        sa.ForeignKeyConstraint(
            ["review_id"],
            ["reviews.id"],
            name="fk_review_revisions_review_id_reviews",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["changed_by_admin_id"],
            ["users.id"],
            name="fk_review_revisions_changed_by_admin_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_review_revisions"),
    )
    op.create_index(
        "ix_review_revisions_review_created",
        "review_revisions",
        ["review_id", "created_at"],
    )


def downgrade() -> None:
    """Refuse to discard revision history silently."""

    bind = op.get_bind()
    count = bind.execute(sa.text("SELECT count(*) FROM review_revisions")).scalar_one()
    if count:
        raise RuntimeError("Refusing downgrade: review_revisions contains audit data")
    op.drop_index("ix_review_revisions_review_created", table_name="review_revisions")
    op.drop_table("review_revisions")
    op.drop_index("ix_reviews_deleted_created", table_name="reviews")
    op.drop_constraint("fk_reviews_deleted_by_user_id_users", "reviews", type_="foreignkey")
    op.drop_constraint("fk_reviews_edited_by_admin_id_users", "reviews", type_="foreignkey")
    for column in (
        "deletion_reason",
        "deleted_by_user_id",
        "deleted_at",
        "is_admin_edited",
        "edited_by_admin_id",
        "edited_at",
    ):
        op.drop_column("reviews", column)
