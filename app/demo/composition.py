"""Dependency composition for demo mode without production services or routers."""

from __future__ import annotations

from typing import cast

from aiogram import Dispatcher
from aiogram.fsm.storage.redis import RedisStorage

from app.config import Settings
from app.demo.handlers import router
from app.demo.middleware import DemoGuardMiddleware, DemoRedis
from app.demo.service import DemoService
from app.middlewares.correlation import CorrelationIdMiddleware


def create_demo_dispatcher(settings: Settings) -> Dispatcher:
    """Build the fail-closed public demo dispatcher."""

    storage = RedisStorage.from_url(
        settings.redis_url.get_secret_value(),
        state_ttl=settings.demo_session_ttl_hours * 60 * 60,
        data_ttl=settings.demo_session_ttl_hours * 60 * 60,
    )
    demo_service = DemoService(timezone=settings.timezone_info)
    dispatcher = Dispatcher(
        storage=storage,
        events_isolation=storage.create_isolation(),
        settings=settings,
        demo_service=demo_service,
    )
    dispatcher.update.outer_middleware(CorrelationIdMiddleware())
    dispatcher.update.outer_middleware(
        DemoGuardMiddleware(
            cast(DemoRedis, storage.redis),
            namespace=settings.redis_namespace,
            user_limit=settings.demo_rate_limit_per_minute,
            global_limit=settings.demo_global_rate_limit_per_minute,
        )
    )
    dispatcher.include_router(router)
    return dispatcher
