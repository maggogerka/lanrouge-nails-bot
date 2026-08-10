"""Dependency readiness checks for Docker and operators."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
from collections.abc import Sequence

from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import RuntimeConfigurationError, Settings, get_settings
from app.logging import configure_logging, log_event
from app.runtime_health import (
    ComponentUnhealthyError,
    check_component_heartbeat,
    runtime_component_names,
)

logger = logging.getLogger(__name__)


class DependencyUnavailableError(RuntimeError):
    """A dependency did not answer its minimal readiness query."""


async def check_database(database_url: str) -> None:
    """Open a fresh database connection and execute a side-effect-free query."""

    engine = create_async_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"server_settings": {"timezone": "UTC"}},
    )
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


async def check_runtime_health(settings: Settings, *, component: str | None = None) -> None:
    """Check shared dependencies and, when selected, this container's heartbeat."""

    await check_dependencies(settings)
    if component is not None:
        await check_component_heartbeat(settings, component)


async def _main(*, component: str | None = None) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        await check_runtime_health(settings, component=component)
    except ComponentUnhealthyError as exc:
        log_event(
            logger,
            logging.ERROR,
            "healthcheck.component_unhealthy",
            component=exc.component,
            status=exc.status,
            error_code=exc.error_code,
        )
        raise
    except Exception:
        logger.exception("dependency_healthcheck_failed", extra={"event": "healthcheck.failed"})
        raise
    log_event(logger, logging.INFO, "healthcheck.ok", component=component)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Dependency and component readiness check")
    parser.add_argument("--component", choices=runtime_component_names())
    return parser


def run(argv: Sequence[str] | None = None) -> None:
    """Synchronous console entry point."""

    args = build_parser().parse_args(argv)
    component = args.component or os.environ.get("HEALTHCHECK_COMPONENT", "").strip() or None
    if component is not None and component not in runtime_component_names():
        raise SystemExit(2)
    try:
        asyncio.run(_main(component=component))
    except RuntimeConfigurationError as exc:
        log_event(logger, logging.CRITICAL, "configuration.invalid", missing=exc.missing)
        raise SystemExit(2) from exc
    except Exception as exc:
        raise SystemExit(1) from exc


if __name__ == "__main__":
    run()
