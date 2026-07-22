"""SQLAlchemy Unit of Work used by application services."""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.audit_repository import AuditRepository
from app.repositories.broadcast_repository import BroadcastRepository
from app.repositories.crm_repository import CrmRepository
from app.repositories.notification_repository import NotificationRepository
from app.repositories.portfolio_repository import PortfolioRepository
from app.repositories.reference_media_repository import ReferenceMediaRepository
from app.repositories.review_repository import ReviewRepository
from app.repositories.service_repository import ServiceRepository
from app.repositories.settings_repository import SettingsRepository
from app.repositories.user_repository import UserRepository
from app.repositories.waitlist_repository import WaitlistRepository
from app.repositories.window_repository import WindowRepository


class SqlAlchemyUnitOfWork:
    """Own one AsyncSession and expose repositories sharing its transaction."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session = session_factory()
        self.appointments = AppointmentRepository(self.session)
        self.notifications = NotificationRepository(self.session)
        self.services = ServiceRepository(self.session)
        self.windows = WindowRepository(self.session)
        self.settings = SettingsRepository(self.session)
        self.users = UserRepository(self.session)
        self.audit = AuditRepository(self.session)
        self.portfolio = PortfolioRepository(self.session)
        self.reference_media = ReferenceMediaRepository(self.session)
        self.crm = CrmRepository(self.session)
        self.waitlist = WaitlistRepository(self.session)
        self.reviews = ReviewRepository(self.session)
        self.broadcasts = BroadcastRepository(self.session)

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
