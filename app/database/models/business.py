"""Business tenant, staff membership, invitations, and typed feature flags."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base
from app.database.models.mixins import TimestampMixin
from app.database.types import database_enum
from app.domain.enums import (
    BusinessStatus,
    BusinessType,
    StaffInvitationStatus,
    StaffRole,
)


class Business(TimestampMixin, Base):
    """A future-proof tenant even though v0.4 deploys one tenant per instance."""

    __tablename__ = "businesses"
    __table_args__ = (
        CheckConstraint(
            "slug ~ '^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$'",
            name="slug_format",
        ),
        CheckConstraint("char_length(currency) = 3", name="currency_iso_length"),
        Index("ix_businesses_status", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    legal_name: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(Text)
    short_description: Mapped[str | None] = mapped_column(String(120))
    business_type: Mapped[BusinessType] = mapped_column(
        database_enum(BusinessType, name="business_type"),
        nullable=False,
        default=BusinessType.SOLO,
        server_default=BusinessType.SOLO.value,
    )
    status: Mapped[BusinessStatus] = mapped_column(
        database_enum(BusinessStatus, name="business_status"),
        nullable=False,
        default=BusinessStatus.SETUP,
        server_default=BusinessStatus.SETUP.value,
    )
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, default="Europe/Moscow", server_default="Europe/Moscow"
    )
    currency: Mapped[str] = mapped_column(
        String(3), nullable=False, default="RUB", server_default="RUB"
    )
    address: Mapped[str | None] = mapped_column(String(500))
    map_url: Mapped[str | None] = mapped_column(String(2048))
    contact_phone: Mapped[str | None] = mapped_column(String(32))
    contact_email: Mapped[str | None] = mapped_column(String(320))
    logo_telegram_file_id: Mapped[str | None] = mapped_column(String(512))
    logo_telegram_file_unique_id: Mapped[str | None] = mapped_column(String(255))
    social_links: Mapped[dict[str, str]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    client_support_name: Mapped[str | None] = mapped_column(String(100))
    client_support_url: Mapped[str | None] = mapped_column(String(2048))
    client_support_hours: Mapped[str | None] = mapped_column(String(255))
    client_support_instructions: Mapped[str | None] = mapped_column(Text)
    privacy_policy_url: Mapped[str | None] = mapped_column(String(2048))
    privacy_policy_version: Mapped[str | None] = mapped_column(String(64))
    privacy_policy_hash: Mapped[str | None] = mapped_column(String(64))
    terms_url: Mapped[str | None] = mapped_column(String(2048))
    terms_version: Mapped[str | None] = mapped_column(String(64))
    terms_hash: Mapped[str | None] = mapped_column(String(64))
    instance_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    setup_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StaffMember(TimestampMixin, Base):
    """A user's revocable role and optional bookable master profile in a business."""

    __tablename__ = "staff_members"
    __table_args__ = (
        CheckConstraint("sort_order BETWEEN -100000 AND 100000", name="sort_order_valid"),
        CheckConstraint("max_daily_appointments > 0", name="daily_limit_positive"),
        Index(
            "uq_staff_members_business_user",
            "business_id",
            "user_id",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        Index(
            "ix_staff_members_business_active_role",
            "business_id",
            "is_active",
            "role",
        ),
        Index(
            "ix_staff_members_business_bookable_order",
            "business_id",
            "is_bookable",
            "sort_order",
            "id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    business_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT")
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    bio: Mapped[str | None] = mapped_column(Text)
    specialization: Mapped[str | None] = mapped_column(String(500))
    telegram_photo_file_id: Mapped[str | None] = mapped_column(String(512))
    telegram_photo_file_unique_id: Mapped[str | None] = mapped_column(String(255))
    role: Mapped[StaffRole] = mapped_column(
        database_enum(StaffRole, name="staff_role"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    is_bookable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    schedule_paused_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    max_daily_appointments: Mapped[int] = mapped_column(
        Integer, nullable=False, default=20, server_default="20"
    )
    settings: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BusinessClient(TimestampMixin, Base):
    """A Telegram identity's client membership in a business tenant."""

    __tablename__ = "business_clients"
    __table_args__ = (
        Index("uq_business_clients_business_user", "business_id", "user_id", unique=True),
        Index("ix_business_clients_business_created", "business_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    business_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    user_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    anonymized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class StaffInvitation(TimestampMixin, Base):
    """Single-use, expiring invitation; only a SHA-256 digest is persisted."""

    __tablename__ = "staff_invitations"
    __table_args__ = (
        CheckConstraint("char_length(token_digest) = 64", name="token_digest_length"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        Index("ix_staff_invitations_business_status", "business_id", "status", "expires_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    business_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    role: Mapped[StaffRole] = mapped_column(
        database_enum(
            StaffRole,
            name="staff_role",
        ),
        nullable=False,
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    is_bookable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    status: Mapped[StaffInvitationStatus] = mapped_column(
        database_enum(StaffInvitationStatus, name="staff_invitation_status"),
        nullable=False,
        default=StaffInvitationStatus.ACTIVE,
        server_default=StaffInvitationStatus.ACTIVE.value,
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_by_staff_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("staff_members.id", ondelete="RESTRICT"), nullable=False
    )
    accepted_by_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT")
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by_staff_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("staff_members.id", ondelete="RESTRICT")
    )


class BusinessFeatureFlags(TimestampMixin, Base):
    """Central typed switches shared by UI, services, and workers."""

    __tablename__ = "business_feature_flags"

    business_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("businesses.id", ondelete="RESTRICT"), primary_key=True
    )
    online_booking: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    master_selection: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    waitlist: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    portfolio: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    reviews: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    reference_photos: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    reminders: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    repeat_booking: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    broadcasts: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    loyalty: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    statistics: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    prepayment: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    manual_payments: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    yookassa_payments: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    mini_app: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    client_support: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
