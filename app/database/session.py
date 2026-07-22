"""Async SQLAlchemy engine and session factory lifecycle."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


@dataclass(slots=True)
class Database:
    """Own the process-wide engine and produce transaction-scoped sessions."""

    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]

    @classmethod
    def create(cls, database_url: str, *, echo: bool = False) -> Database:
        engine = create_async_engine(
            database_url,
            echo=echo,
            hide_parameters=True,
            pool_pre_ping=True,
            pool_recycle=1800,
            connect_args={"server_settings": {"timezone": "UTC"}},
        )
        sessions = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
        )
        return cls(engine=engine, sessions=sessions)

    async def close(self) -> None:
        """Dispose pooled database connections during graceful shutdown."""

        await self.engine.dispose()
