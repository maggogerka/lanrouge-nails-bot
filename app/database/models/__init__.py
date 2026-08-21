"""ORM model registry imported by Alembic and application repositories."""

from app.database.models.appointment import Appointment, AppointmentStatusHistory
from app.database.models.appointment_reference import (
    AppointmentReferenceMedia,
    ReferenceCleanupState,
)
from app.database.models.audit import AuditLog
from app.database.models.availability_window import AvailabilityWindow
from app.database.models.broadcast import (
    Broadcast,
    BroadcastMedia,
    BroadcastRecipient,
    MarketingEvent,
)
from app.database.models.business import (
    Business,
    BusinessClient,
    BusinessFeatureFlags,
    StaffInvitation,
    StaffMember,
)
from app.database.models.commerce import (
    BookingReservation,
    BusinessPaymentSettings,
    BusinessSubscription,
)
from app.database.models.crm import ClientNote, ClientTag, ConsentHistory, UserClientTag
from app.database.models.master_profile import MasterProfile, MasterPublicLink
from app.database.models.notification import NotificationJob
from app.database.models.payment import Payment, PaymentWebhookEvent, Refund
from app.database.models.portfolio import (
    PortfolioItem,
    PortfolioItemTag,
    PortfolioMedia,
    PortfolioTag,
)
from app.database.models.privacy import (
    AcquisitionSource,
    ClientAcquisitionAttribution,
    DataDeletionRequest,
    DataDeletionRequestEvent,
)
from app.database.models.review import Review, ReviewRevision
from app.database.models.schedule import StaffScheduleException, StaffWeeklyInterval
from app.database.models.service import Service
from app.database.models.service_addon import AppointmentAddonSnapshot, ServiceAddon
from app.database.models.service_assignment import ServiceCategory, StaffServiceAssignment
from app.database.models.settings import BusinessSettings
from app.database.models.user import User
from app.database.models.waitlist import WaitlistEntry, WaitlistNotification
from app.database.models.workstation import Workstation, WorkstationService

__all__ = [
    "AcquisitionSource",
    "Appointment",
    "AppointmentAddonSnapshot",
    "AppointmentReferenceMedia",
    "AppointmentStatusHistory",
    "AuditLog",
    "AvailabilityWindow",
    "BookingReservation",
    "Broadcast",
    "BroadcastMedia",
    "BroadcastRecipient",
    "Business",
    "BusinessClient",
    "BusinessFeatureFlags",
    "BusinessPaymentSettings",
    "BusinessSettings",
    "BusinessSubscription",
    "ClientAcquisitionAttribution",
    "ClientNote",
    "ClientTag",
    "ConsentHistory",
    "DataDeletionRequest",
    "DataDeletionRequestEvent",
    "MarketingEvent",
    "MasterProfile",
    "MasterPublicLink",
    "NotificationJob",
    "Payment",
    "PaymentWebhookEvent",
    "PortfolioItem",
    "PortfolioItemTag",
    "PortfolioMedia",
    "PortfolioTag",
    "ReferenceCleanupState",
    "Refund",
    "Review",
    "ReviewRevision",
    "Service",
    "ServiceAddon",
    "ServiceCategory",
    "StaffInvitation",
    "StaffMember",
    "StaffScheduleException",
    "StaffServiceAssignment",
    "StaffWeeklyInterval",
    "User",
    "UserClientTag",
    "WaitlistEntry",
    "WaitlistNotification",
    "Workstation",
    "WorkstationService",
]
