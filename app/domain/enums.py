"""Explicit persisted enums used by the v0.1.0 domain."""

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
