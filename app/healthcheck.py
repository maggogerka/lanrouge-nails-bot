"""Dependency readiness checks for Docker and operators."""

from __future__ import annotations

import asyncio
import logging

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import Settings, get_settings
from app.logging import configure_logging, log_event

logger = logging.getLogger(__name__)


class DependencyUnavailableError(RuntimeError):
    """A dependency did not answer its minimal readiness query."""


async def check_database(database_url: str) -> None:
    """Open a fresh database connection and execute a side-effect-free query."""

    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception as exc:
        raise DependencyUnavailableError("PostgreSQL readiness check failed") from exc
    finally:
        await engine.dispose()


async def check_redis(redis_url: str) -> None:
    """Ping Redis with a short-lived client."""

    client: Redis = Redis.from_url(redis_url, socket_connect_timeout=5, socket_timeout=5)
    try:
        if not await client.ping():
            raise DependencyUnavailableError("Redis readiness check returned false")
    except DependencyUnavailableError:
        raise
    except Exception as exc:
        raise DependencyUnavailableError("Redis readiness check failed") from exc
    finally:
        await client.aclose()


async def check_dependencies(settings: Settings) -> None:
    """Check PostgreSQL and Redis concurrently."""

    settings.validate_dependency_runtime()
    await asyncio.gather(
        check_database(settings.database_url.get_secret_value()),
        check_redis(settings.redis_url.get_secret_value()),
    )


async def _main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        await check_dependencies(settings)
    except Exception:
        logger.exception("dependency_healthcheck_failed", extra={"event": "healthcheck.failed"})
        raise
    log_event(logger, logging.INFO, "healthcheck.ok")


def run() -> None:
    """Synchronous console entry point."""

    asyncio.run(_main())


if __name__ == "__main__":
    run()
