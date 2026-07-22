"""Explicit persisted enums used by the booking and CRM domains."""

from enum import StrEnum


class UserRole(StrEnum):
    """Descriptive role; administrative authority still comes from env IDs."""

    CLIENT = "client"
    ADMIN = "admin"


class AvailabilityWindowStatus(StrEnum):
    """Lifecycle of a manually created availability window."""

    OPEN = "open"
    RESERVED = "reserved"
    BOOKED = "booked"
    CLOSED = "closed"
    EXPIRED = "expired"


class AppointmentStatus(StrEnum):
    """Business-visible appointment states."""

    CONFIRMED = "confirmed"
    CLIENT_CONFIRMED = "client_confirmed"
    COMPLETED = "completed"
    CANCELLED_BY_CLIENT = "cancelled_by_client"
    CANCELLED_BY_ADMIN = "cancelled_by_admin"
    NO_SHOW = "no_show"
    RESCHEDULED = "rescheduled"


class NotificationJobStatus(StrEnum):
    """Persistent reminder delivery state."""

    PENDING = "pending"
    PROCESSING = "processing"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"


class NotificationType(StrEnum):
    """Recipient-specific notification templates."""

    CLIENT_REMINDER = "client_reminder"
    ADMIN_REMINDER = "admin_reminder"
    REVIEW_REQUEST = "review_request"
    REPEAT_BOOKING_REMINDER = "repeat_booking_reminder"


class PortfolioStatus(StrEnum):
    """Publication lifecycle of a portfolio work."""

    DRAFT = "draft"
    PUBLISHED = "published"
    ARCHIVED = "archived"


class PortfolioDisplayMode(StrEnum):
    """Client-facing portfolio destination without deleting internal works."""

    INTERNAL = "internal"
    EXTERNAL_LINK = "external_link"
    DISABLED = "disabled"


class MediaType(StrEnum):
    """Telegram media supported by v0.2.0."""

    PHOTO = "photo"


class ConsentType(StrEnum):
    """Independently managed user permissions."""

    PRIVACY = "privacy"
    MARKETING = "marketing"
    REPEAT_BOOKING = "repeat_booking"


class ConsentSource(StrEnum):
    """Origin of an auditable consent change."""

    ONBOARDING = "onboarding"
    NOTIFICATION_SETTINGS = "notification_settings"
    ADMIN = "admin"
    SYSTEM = "system"


class WaitlistStatus(StrEnum):
    """Lifecycle of a client's waitlist request."""

    ACTIVE = "active"
    MATCHED = "matched"
    BOOKED = "booked"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class WaitlistNotificationStatus(StrEnum):
    """Delivery lifecycle for a waitlist entry/window pair."""

    PENDING = "pending"
    PROCESSING = "processing"
    SENT = "sent"
    RETRY = "retry"
    FAILED = "failed"
    CANCELLED = "cancelled"


class ReviewModerationStatus(StrEnum):
    """Publication moderation without editing client content."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    HIDDEN = "hidden"


class BroadcastStatus(StrEnum):
    """Campaign preparation and delivery lifecycle."""

    DRAFT = "draft"
    SCHEDULED = "scheduled"
    PREPARING = "preparing"
    SENDING = "sending"
    COMPLETED = "completed"
    PARTIALLY_FAILED = "partially_failed"
    CANCELLED = "cancelled"
    FAILED = "failed"


class BroadcastRecipientStatus(StrEnum):
    """Per-recipient delivery state frozen at campaign confirmation."""

    PENDING = "pending"
    PROCESSING = "processing"
    SENT = "sent"
    RETRY = "retry"
    FAILED = "failed"
    SKIPPED = "skipped"
    UNSUBSCRIBED = "unsubscribed"
    BLOCKED = "blocked"


class BroadcastAudienceType(StrEnum):
    """Supported audience selectors persisted with a broadcast."""

    ALL_SUBSCRIBED = "all_subscribed"
    CLIENT_TAG = "client_tag"
    SERVICE_HISTORY = "service_history"
    INACTIVE_DAYS = "inactive_days"
    MANUAL = "manual"


class BroadcastButtonType(StrEnum):
    """Validated call-to-action rendered below a broadcast."""

    NONE = "none"
    BOOK = "book"
    PORTFOLIO = "portfolio"
    AVAILABLE_WINDOWS = "available_windows"
    URL = "url"


class MarketingEventType(StrEnum):
    """Internal callback interactions available from Telegram."""

    BOOKING_CLICKED = "booking_clicked"
    PORTFOLIO_CLICKED = "portfolio_clicked"
    WINDOWS_CLICKED = "windows_clicked"
