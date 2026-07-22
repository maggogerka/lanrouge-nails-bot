"""SQLAlchemy Unit of Work used by application services."""

from __future__ import annotations

from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.repositories.audit_repository import AuditRepository
from app.repositories.service_repository import ServiceRepository
from app.repositories.settings_repository import SettingsRepository
from app.repositories.user_repository import UserRepository
from app.repositories.window_repository import WindowRepository


class SqlAlchemyUnitOfWork:
    """Own one AsyncSession and expose repositories sharing its transaction."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self.session = session_factory()
        self.services = ServiceRepository(self.session)
        self.windows = WindowRepository(self.session)
        self.settings = SettingsRepository(self.session)
        self.users = UserRepository(self.session)
        self.audit = AuditRepository(self.session)

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
