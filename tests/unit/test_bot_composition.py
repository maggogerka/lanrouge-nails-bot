"""Composition-root tests without opening network connections."""

from __future__ import annotations

import pytest
from aiogram.fsm.storage.redis import RedisStorage

from app.bot import create_dispatcher
from app.config import Settings
from app.database import Database
from app.handlers import root_router
from app.handlers.errors import handle_unexpected_error


def make_settings() -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        BOT_TOKEN="123456:development-token",
        DATABASE_URL="postgresql+asyncpg://user:password@localhost/database",
        REDIS_URL="redis://localhost:6379/0",
    )


@pytest.mark.asyncio
async def test_dispatcher_uses_redis_and_includes_root_router() -> None:
    database = Database.create("postgresql+asyncpg://user:password@localhost/database")
    dispatcher = create_dispatcher(make_settings(), database)

    assert isinstance(dispatcher.storage, RedisStorage)
    assert dispatcher.storage.state_ttl == 24 * 60 * 60
    assert dispatcher.storage.data_ttl == 24 * 60 * 60
    assert root_router in dispatcher.sub_routers

    await dispatcher.storage.close()
    await database.close()


def test_error_boundary_is_registered_on_root_router() -> None:
    callbacks = [handler.callback for handler in root_router.errors.handlers]

    assert handle_unexpected_error in callbacks
