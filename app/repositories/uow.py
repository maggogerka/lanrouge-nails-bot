"""SQLAlchemy Unit of Work used by application services."""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain.tenancy import DEFAULT_BUSINESS_ID
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.audit_repository import AuditRepository
from app.repositories.broadcast_repository import BroadcastRepository
from app.repositories.business_repository import BusinessRepository
from app.repositories.crm_repository import CrmRepository
from app.repositories.feature_repository import FeatureRepository
from app.repositories.hard_delete_repository import HardDeleteRepository
from app.repositories.master_profile_repository import MasterProfileRepository
from app.repositories.notification_repository import NotificationRepository
from app.repositories.payment_repository import PaymentRepository
from app.repositories.portfolio_repository import PortfolioRepository
from app.repositories.privacy_repository import PrivacyRepository
from app.repositories.reference_media_repository import ReferenceMediaRepository
from app.repositories.reservation_repository import ReservationRepository
from app.repositories.review_repository import ReviewRepository
from app.repositories.schedule_repository import ScheduleRepository
from app.repositories.service_addon_repository import ServiceAddonRepository
from app.repositories.service_assignment_repository import ServiceAssignmentRepository
from app.repositories.service_repository import ServiceRepository
from app.repositories.settings_repository import SettingsRepository
from app.repositories.staff_repository import StaffRepository
from app.repositories.subscription_repository import SubscriptionRepository
from app.repositories.user_repository import UserRepository
from app.repositories.waitlist_repository import WaitlistRepository
from app.repositories.window_repository import WindowRepository
from app.repositories.workstation_repository import WorkstationRepository


class SqlAlchemyUnitOfWork:
    """Own one AsyncSession and expose repositories sharing its transaction."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        business_id: int = DEFAULT_BUSINESS_ID,
    ) -> None:
        if business_id <= 0:
            raise ValueError("business_id must be positive")
        self.business_id = business_id
        self.session = session_factory()
        self.appointments = AppointmentRepository(self.session, business_id)
        self.notifications = NotificationRepository(self.session, business_id)
        self.services = ServiceRepository(self.session, business_id)
        self.service_addons = ServiceAddonRepository(self.session, business_id)
        self.service_assignments = ServiceAssignmentRepository(self.session)
        self.schedules = ScheduleRepository(self.session)
        self.windows = WindowRepository(self.session, business_id)
        self.workstations = WorkstationRepository(self.session, business_id)
        self.settings = SettingsRepository(self.session, business_id)
        self.staff = StaffRepository(self.session)
        self.subscriptions = SubscriptionRepository(self.session, business_id)
        self.users = UserRepository(self.session, business_id)
        self.audit = AuditRepository(self.session, business_id)
        self.portfolio = PortfolioRepository(self.session, business_id)
        self.privacy = PrivacyRepository(self.session, business_id=business_id)
        self.reference_media = ReferenceMediaRepository(self.session, business_id)
        self.crm = CrmRepository(self.session, business_id)
        self.features = FeatureRepository(self.session, business_id)
        self.hard_delete = HardDeleteRepository(self.session, business_id)
        self.master_profile = MasterProfileRepository(self.session, business_id)
        self.waitlist = WaitlistRepository(self.session, business_id)
        self.reviews = ReviewRepository(self.session, business_id)
        self.broadcasts = BroadcastRepository(self.session, business_id)
        self.businesses = BusinessRepository(self.session, business_id)
        self.payments = PaymentRepository(self.session, business_id)
        self.reservations = ReservationRepository(self.session, business_id)

    async def __aenter__(self) -> SqlAlchemyUnitOfWork:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        await self.session.rollback()
        await self.session.close()

    async def commit(self) -> None:
        """Commit the current transaction explicitly."""

        await self.session.commit()
