"""Database factory lifecycle tests that do not open a connection."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from app.config import Settings
from app.database.session import Database


@pytest.mark.asyncio
async def test_database_factory_uses_asyncpg_and_closes() -> None:
    database = Database.create("postgresql+asyncpg://user:password@localhost/database")

    assert database.engine.url.drivername == "postgresql+asyncpg"
    assert database.engine.sync_engine.hide_parameters is True
    assert database.sessions.kw["expire_on_commit"] is False

    await database.close()


@pytest.mark.asyncio
async def test_database_factory_applies_bounded_pool_and_server_timeouts() -> None:
    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql+asyncpg://user:password@localhost/database",
        DATABASE_POOL_SIZE=3,
        DATABASE_MAX_OVERFLOW=1,
        DATABASE_POOL_TIMEOUT_SECONDS=7,
        DATABASE_STATEMENT_TIMEOUT_MS=12_000,
        DATABASE_LOCK_TIMEOUT_MS=2_500,
        DATABASE_IDLE_IN_TRANSACTION_TIMEOUT_MS=9_000,
    )

    with patch(
        "app.database.session.create_async_engine",
        wraps=__import__(
            "sqlalchemy.ext.asyncio", fromlist=["create_async_engine"]
        ).create_async_engine,
    ) as create_engine:
        database = Database.from_settings(settings)

    kwargs = create_engine.call_args.kwargs
    assert kwargs["pool_size"] == 3
    assert kwargs["max_overflow"] == 1
    assert kwargs["pool_timeout"] == 7
    assert kwargs["connect_args"]["server_settings"] == {
        "timezone": "UTC",
        "statement_timeout": "12000ms",
        "lock_timeout": "2500ms",
        "idle_in_transaction_session_timeout": "9000ms",
    }
    await database.close()
