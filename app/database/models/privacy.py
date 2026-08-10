"""Privacy-deletion workflow and tenant-scoped acquisition attribution."""

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
from app.domain.enums import DataDeletionRequestStatus


class DataDeletionRequest(TimestampMixin, Base):
    """Idempotent, auditable request to anonymize one business client."""

    __tablename__ = "data_deletion_requests"
    __table_args__ = (
        Index(
            "uq_data_deletion_requests_open_client",
            "business_id",
            "business_client_id",
            unique=True,
            postgresql_where=text("status IN ('requested', 'in_review', 'approved')"),
        ),
        Index(
            "ix_data_deletion_requests_business_status_requested",
            "business_id",
            "status",
            "requested_at",
        ),
        CheckConstraint(
            "correlation_id IS NULL OR char_length(correlation_id) BETWEEN 1 AND 64",
            name="correlation_id_length_valid",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    business_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    business_client_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("business_clients.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[DataDeletionRequestStatus] = mapped_column(
        database_enum(
            DataDeletionRequestStatus,
            name="data_deletion_request_status",
        ),
        nullable=False,
        default=DataDeletionRequestStatus.REQUESTED,
        server_default=DataDeletionRequestStatus.REQUESTED.value,
    )
    request_reason_code: Mapped[str] = mapped_column(
        String(100), nullable=False, default="client_request", server_default="client_request"
    )
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    processed_by_staff_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("staff_members.id", ondelete="RESTRICT")
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retention_reason: Mapped[str | None] = mapped_column(Text)
    result_code: Mapped[str | None] = mapped_column(String(100))
    anonymization_plan: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    anonymization_result: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )


class DataDeletionRequestEvent(Base):
    """Append-only, PII-free status trail for a deletion request."""

    __tablename__ = "data_deletion_request_events"
    __table_args__ = (
        Index(
            "ix_data_deletion_request_events_request_created",
            "request_id",
            "created_at",
        ),
        CheckConstraint(
            "previous_status IS NOT NULL OR new_status = 'requested'",
            name="initial_event_is_requested",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    business_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    request_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("data_deletion_requests.id", ondelete="RESTRICT"), nullable=False
    )
    previous_status: Mapped[DataDeletionRequestStatus | None] = mapped_column(
        database_enum(
            DataDeletionRequestStatus,
            name="data_deletion_request_status",
        )
    )
    new_status: Mapped[DataDeletionRequestStatus] = mapped_column(
        database_enum(
            DataDeletionRequestStatus,
            name="data_deletion_request_status",
        ),
        nullable=False,
    )
    actor_user_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT")
    )
    actor_staff_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("staff_members.id", ondelete="RESTRICT")
    )
    safe_details: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AcquisitionSource(TimestampMixin, Base):
    """Business-owned campaign/source code accepted in Telegram deep links."""

    __tablename__ = "acquisition_sources"
    __table_args__ = (
        Index("uq_acquisition_sources_business_code", "business_id", "code", unique=True),
        Index("ix_acquisition_sources_business_active", "business_id", "is_active"),
        CheckConstraint(
            "code ~ '^[a-z0-9][a-z0-9_-]{0,63}$'",
            name="code_format",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    business_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    channel: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_by_staff_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("staff_members.id", ondelete="RESTRICT")
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ClientAcquisitionAttribution(TimestampMixin, Base):
    """Compact first/last-touch projection; first touch is immutable in the service."""

    __tablename__ = "client_acquisition_attributions"
    __table_args__ = (
        Index(
            "uq_client_acquisition_attributions_business_client",
            "business_id",
            "business_client_id",
            unique=True,
        ),
        Index(
            "ix_client_acquisition_attributions_business_last_source",
            "business_id",
            "last_source_id",
            "last_touched_at",
        ),
        CheckConstraint("touch_count >= 1", name="touch_count_positive"),
        CheckConstraint(
            "last_touched_at >= first_touched_at",
            name="touch_order_valid",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    business_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("businesses.id", ondelete="RESTRICT"), nullable=False
    )
    business_client_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("business_clients.id", ondelete="RESTRICT"), nullable=False
    )
    first_source_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("acquisition_sources.id", ondelete="RESTRICT"), nullable=False
    )
    first_touched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_source_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("acquisition_sources.id", ondelete="RESTRICT"), nullable=False
    )
    last_touched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    touch_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
