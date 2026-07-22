"""Database factory lifecycle tests that do not open a connection."""

from __future__ import annotations

import pytest

from app.database.session import Database


@pytest.mark.asyncio
async def test_database_factory_uses_asyncpg_and_closes() -> None:
    database = Database.create("postgresql+asyncpg://user:password@localhost/database")

    assert database.engine.url.drivername == "postgresql+asyncpg"
    assert database.sessions.kw["expire_on_commit"] is False

    await database.close()
