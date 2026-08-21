"""Redis-only readiness probe for the database-free public demo."""

from __future__ import annotations

import asyncio

from redis.asyncio import Redis

from app.config import get_settings
from app.runtime_health import check_component_heartbeat


async def _main() -> None:
    settings = get_settings()
    settings.validate_bot_runtime()
    if not settings.is_demo:
        raise RuntimeError("demo healthcheck requires APP_MODE=demo")
    redis = Redis.from_url(
        settings.redis_url.get_secret_value(),
        socket_connect_timeout=5,
        socket_timeout=5,
    )
    try:
        if not await redis.ping():
            raise RuntimeError("demo Redis readiness failed")
    finally:
        await redis.aclose()
    await check_component_heartbeat(settings, "bot")


def run() -> None:
    asyncio.run(_main())


if __name__ == "__main__":
    run()
