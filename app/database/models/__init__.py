"""ORM model registry imported by Alembic and application repositories."""

from app.database.models.appointment import Appointment, AppointmentStatusHistory
from app.database.models.appointment_reference import AppointmentReferenceMedia
from app.database.models.audit import AuditLog
from app.database.models.availability_window import AvailabilityWindow
from app.database.models.broadcast import (
    Broadcast,
    BroadcastMedia,
    BroadcastRecipient,
    MarketingEvent,
)
from app.database.models.crm import ClientNote, ClientTag, ConsentHistory, UserClientTag
from app.database.models.notification import NotificationJob
from app.database.models.portfolio import (
    PortfolioItem,
    PortfolioItemTag,
    PortfolioMedia,
    PortfolioTag,
)
from app.database.models.review import Review, ReviewRevision
from app.database.models.service import Service
from app.database.models.settings import BusinessSettings
from app.database.models.user import User
from app.database.models.waitlist import WaitlistEntry, WaitlistNotification

__all__ = [
    "Appointment",
    "AppointmentReferenceMedia",
    "AppointmentStatusHistory",
    "AuditLog",
    "AvailabilityWindow",
    "Broadcast",
    "BroadcastMedia",
    "BroadcastRecipient",
    "BusinessSettings",
    "ClientNote",
    "ClientTag",
    "ConsentHistory",
    "MarketingEvent",
    "NotificationJob",
    "PortfolioItem",
    "PortfolioItemTag",
    "PortfolioMedia",
    "PortfolioTag",
    "Review",
    "ReviewRevision",
    "Service",
    "User",
    "UserClientTag",
    "WaitlistEntry",
    "WaitlistNotification",
]
