"""Periodic deletion of stale public-demo workspaces only."""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Sequence

from app.config import get_settings
from app.database import Database
from app.demo.service import DemoService
from app.logging import configure_logging, log_event

logger = logging.getLogger(__name__)


async def cleanup_once() -> int:
    settings = get_settings()
    settings.validate_database_runtime()
    if not settings.is_demo:
        raise RuntimeError("demo cleanup refuses to run outside APP_MODE=demo")
    database = Database.from_settings(settings)
    try:
        service = DemoService(
            database.sessions,
            timezone=settings.timezone_info,
            retention_hours=settings.demo_data_retention_hours,
        )
        deleted = await service.cleanup_expired()
        log_event(logger, logging.INFO, "demo.cleanup_completed", deleted_count=deleted)
        return deleted
    finally:
        await database.close()


async def run_loop(interval_seconds: int) -> None:
    while True:
        await cleanup_once()
        await asyncio.sleep(interval_seconds)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Delete stale public-demo workspaces")
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-seconds", type=int, default=3600)
    return parser


def run(argv: Sequence[str] | None = None) -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    args = build_parser().parse_args(argv)
    if not 60 <= args.interval_seconds <= 86_400:
        raise SystemExit("--interval-seconds must be between 60 and 86400")
    if args.loop:
        asyncio.run(run_loop(args.interval_seconds))
    else:
        asyncio.run(cleanup_once())


if __name__ == "__main__":
    run()
