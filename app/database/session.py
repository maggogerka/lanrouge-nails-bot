"""Async SQLAlchemy engine and session factory lifecycle."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

if TYPE_CHECKING:
    from app.config import Settings


@dataclass(slots=True)
class Database:
    """Own the process-wide engine and produce transaction-scoped sessions."""

    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]

    @classmethod
    def create(
        cls,
        database_url: str,
        *,
        echo: bool = False,
        pool_size: int = 2,
        max_overflow: int = 2,
        pool_timeout_seconds: float = 10.0,
        statement_timeout_ms: int = 30_000,
        lock_timeout_ms: int = 5_000,
        idle_in_transaction_timeout_ms: int = 30_000,
    ) -> Database:
        server_settings = {
            "timezone": "UTC",
            "statement_timeout": f"{statement_timeout_ms}ms",
            "lock_timeout": f"{lock_timeout_ms}ms",
            "idle_in_transaction_session_timeout": (f"{idle_in_transaction_timeout_ms}ms"),
        }
        engine = create_async_engine(
            database_url,
            echo=echo,
            hide_parameters=True,
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_size=pool_size,
            max_overflow=max_overflow,
            pool_timeout=pool_timeout_seconds,
            connect_args={"server_settings": server_settings},
        )
        sessions = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        return cls(engine=engine, sessions=sessions)

    @classmethod
    def from_settings(cls, settings: Settings, *, echo: bool = False) -> Database:
        """Build the process-wide pool from validated bounded runtime settings."""

        return cls.create(
            settings.database_url.get_secret_value(),
            echo=echo,
            pool_size=settings.database_pool_size,
            max_overflow=settings.database_max_overflow,
            pool_timeout_seconds=settings.database_pool_timeout_seconds,
            statement_timeout_ms=settings.database_statement_timeout_ms,
            lock_timeout_ms=settings.database_lock_timeout_ms,
            idle_in_transaction_timeout_ms=(settings.database_idle_in_transaction_timeout_ms),
        )

    async def close(self) -> None:
        """Dispose pooled database connections during graceful shutdown."""

        await self.engine.dispose()
