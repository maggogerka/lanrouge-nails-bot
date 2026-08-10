"""Database metadata contracts independent of a running PostgreSQL server."""

from __future__ import annotations

from sqlalchemy import DateTime, Enum, Numeric
from sqlalchemy.dialects.postgresql import ExcludeConstraint

from app.database.base import Base
from app.database.models import (
    Appointment,
    AppointmentReferenceMedia,
    AppointmentStatusHistory,
    AuditLog,
    AvailabilityWindow,
    Broadcast,
    BroadcastMedia,
    BroadcastRecipient,
    BusinessSettings,
    ClientNote,
    ClientTag,
    ConsentHistory,
    MarketingEvent,
    MasterProfile,
    MasterPublicLink,
    NotificationJob,
    PortfolioItem,
    PortfolioItemTag,
    PortfolioMedia,
    PortfolioTag,
    ReferenceCleanupState,
    Review,
    ReviewRevision,
    Service,
    User,
    UserClientTag,
    WaitlistEntry,
    WaitlistNotification,
)
from app.domain.enums import AppointmentStatus, AvailabilityWindowStatus, PortfolioStatus

EXPECTED_TABLES = {
    "acquisition_sources",
    "appointment_reference_media",
    "appointment_status_history",
    "appointments",
    "audit_logs",
    "availability_windows",
    "booking_reservations",
    "broadcast_media",
    "broadcast_recipients",
    "broadcasts",
    "business_clients",
    "business_feature_flags",
    "business_payment_settings",
    "business_settings",
    "business_subscriptions",
    "businesses",
    "client_acquisition_attributions",
    "client_tags",
    "client_notes",
    "consent_history",
    "data_deletion_request_events",
    "data_deletion_requests",
    "marketing_events",
    "master_profiles",
    "master_public_links",
    "notification_jobs",
    "payment_webhook_events",
    "payments",
    "portfolio_item_tags",
    "portfolio_items",
    "portfolio_media",
    "portfolio_tags",
    "reference_cleanup_state",
    "refunds",
    "review_revisions",
    "reviews",
    "service_categories",
    "services",
    "staff_invitations",
    "staff_members",
    "staff_schedule_exceptions",
    "staff_service_assignments",
    "staff_weekly_intervals",
    "user_client_tags",
    "users",
    "waitlist_entries",
    "waitlist_notifications",
}


def test_required_tables_are_registered() -> None:
    assert set(Base.metadata.tables) == EXPECTED_TABLES
    assert User.__table__.name == "users"
    assert Service.__table__.name == "services"
    assert AvailabilityWindow.__table__.name == "availability_windows"
    assert Appointment.__table__.name == "appointments"
    assert AppointmentStatusHistory.__table__.name == "appointment_status_history"
    assert AppointmentReferenceMedia.__table__.name == "appointment_reference_media"
    assert NotificationJob.__table__.name == "notification_jobs"
    assert BusinessSettings.__table__.name == "business_settings"
    assert AuditLog.__table__.name == "audit_logs"
    assert PortfolioItem.__table__.name == "portfolio_items"
    assert PortfolioMedia.__table__.name == "portfolio_media"
    assert PortfolioTag.__table__.name == "portfolio_tags"
    assert PortfolioItemTag.__table__.name == "portfolio_item_tags"
    assert ClientTag.__table__.name == "client_tags"
    assert UserClientTag.__table__.name == "user_client_tags"
    assert ClientNote.__table__.name == "client_notes"
    assert ConsentHistory.__table__.name == "consent_history"
    assert WaitlistEntry.__table__.name == "waitlist_entries"
    assert WaitlistNotification.__table__.name == "waitlist_notifications"
    assert Review.__table__.name == "reviews"
    assert ReviewRevision.__table__.name == "review_revisions"
    assert Broadcast.__table__.name == "broadcasts"
    assert BroadcastMedia.__table__.name == "broadcast_media"
    assert BroadcastRecipient.__table__.name == "broadcast_recipients"
    assert MarketingEvent.__table__.name == "marketing_events"
    assert MasterProfile.__table__.name == "master_profiles"
    assert MasterPublicLink.__table__.name == "master_public_links"
    assert ReferenceCleanupState.__table__.name == "reference_cleanup_state"


def test_every_datetime_column_is_timezone_aware() -> None:
    datetime_columns = [
        column
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, DateTime)
    ]

    assert datetime_columns
    assert all(column.type.timezone for column in datetime_columns)


def test_money_columns_are_fixed_precision_decimal() -> None:
    service_price = Service.__table__.c.price.type
    snapshot_price = Appointment.__table__.c.price_snapshot.type
    design_price = PortfolioItem.__table__.c.design_price.type

    assert isinstance(service_price, Numeric)
    assert isinstance(snapshot_price, Numeric)
    assert isinstance(design_price, Numeric)
    assert (service_price.precision, service_price.scale) == (12, 2)
    assert (snapshot_price.precision, snapshot_price.scale) == (12, 2)
    assert (design_price.precision, design_price.scale) == (12, 2)
    assert service_price.asdecimal
    assert snapshot_price.asdecimal
    assert design_price.asdecimal


def test_database_enums_persist_public_values() -> None:
    window_type = AvailabilityWindow.__table__.c.status.type
    appointment_type = Appointment.__table__.c.status.type
    portfolio_type = PortfolioItem.__table__.c.status.type

    assert isinstance(window_type, Enum)
    assert isinstance(appointment_type, Enum)
    assert isinstance(portfolio_type, Enum)
    assert window_type.enums == [status.value for status in AvailabilityWindowStatus]
    assert appointment_type.enums == [status.value for status in AppointmentStatus]
    assert portfolio_type.enums == [status.value for status in PortfolioStatus]


def test_active_windows_have_database_overlap_protection() -> None:
    exclusion_constraints = [
        constraint
        for constraint in AvailabilityWindow.__table__.constraints
        if isinstance(constraint, ExcludeConstraint)
    ]

    assert [constraint.name for constraint in exclusion_constraints] == [
        "ex_availability_windows_active_overlap"
    ]


def test_occupied_window_has_partial_unique_index() -> None:
    index = next(
        index
        for index in Appointment.__table__.indexes
        if index.name == "uq_appointments_occupied_window"
    )

    assert index.unique
    assert index.dialect_options["postgresql"]["where"] is not None


def test_notification_delivery_key_is_unique() -> None:
    constraint_names = {constraint.name for constraint in NotificationJob.__table__.constraints}

    assert "uq_notification_jobs_delivery" in constraint_names


def test_v020_delivery_and_ownership_keys_are_unique() -> None:
    waitlist_constraints = {
        constraint.name for constraint in WaitlistNotification.__table__.constraints
    }
    broadcast_constraints = {
        constraint.name for constraint in BroadcastRecipient.__table__.constraints
    }
    review_constraints = {constraint.name for constraint in Review.__table__.constraints}

    assert "uq_waitlist_notifications_match" in waitlist_constraints
    assert "uq_broadcast_recipients_user" in broadcast_constraints
    assert "uq_reviews_appointment_id" in review_constraints


def test_reference_media_has_stable_order_and_deduplication() -> None:
    constraints = {
        constraint.name for constraint in AppointmentReferenceMedia.__table__.constraints
    }

    assert "uq_appointment_reference_position" in constraints
    assert "uq_appointment_reference_file" in constraints
    assert "telegram_file_id" in AppointmentReferenceMedia.__table__.c


def test_review_revision_is_registered_and_restricts_review_deletion() -> None:
    assert ReviewRevision.__table__.c.review_id.foreign_keys
    foreign_key = next(iter(ReviewRevision.__table__.c.review_id.foreign_keys))
    assert foreign_key.ondelete == "RESTRICT"


def test_foreign_keys_restrict_history_deletion() -> None:
    foreign_keys = [
        foreign_key for table in Base.metadata.tables.values() for foreign_key in table.foreign_keys
    ]

    assert foreign_keys
    assert all(foreign_key.ondelete == "RESTRICT" for foreign_key in foreign_keys)
