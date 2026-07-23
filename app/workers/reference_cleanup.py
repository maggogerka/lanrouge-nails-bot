"""Periodic retention worker for Telegram-hosted appointment references."""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.config import RuntimeConfigurationError, Settings, get_settings
from app.database import Database
from app.logging import configure_logging, log_event
from app.repositories import SqlAlchemyUnitOfWork
from app.services.reference_cleanup_service import ReferenceCleanupService

logger = logging.getLogger(__name__)


def disk_free_percent(path: Path) -> float:
    usage = shutil.disk_usage(path)
    return usage.free * 100 / usage.total if usage.total else 0.0


async def run_cleanup_cycle(service: ReferenceCleanupService, *, now: datetime) -> None:
    health = await service.health()
    latest_healthy_run = health.last_succeeded_at or health.last_started_at
    if latest_healthy_run is not None and latest_healthy_run < now - timedelta(hours=24):
        log_event(logger, logging.WARNING, "reference_cleanup.stale")
    if health.consecutive_failures >= 3:
        log_event(
            logger,
            logging.WARNING,
            "reference_cleanup.repeated_failures",
            failure_count=health.consecutive_failures,
            error_code=health.last_error_code,
        )
    free_percent = disk_free_percent(Path.cwd())
    if free_percent < 15:
        log_event(
            logger,
            logging.WARNING,
            "reference_cleanup.low_disk_space",
            free_percent=round(free_percent, 2),
        )
    result = await service.run(dry_run=False, now=now)
    log_event(
        logger,
        logging.INFO if not result.errors else logging.WARNING,
        "reference_cleanup.completed",
        checked=result.checked,
        deleted=result.deleted,
        estimated_bytes_released=result.estimated_bytes_released,
        error_count=result.errors,
        duration_seconds=round(result.duration_seconds, 3),
    )


async def run_worker(settings: Settings) -> None:
    settings.validate_database_runtime()
    database = Database.create(settings.database_url.get_secret_value())
    service = ReferenceCleanupService(lambda: SqlAlchemyUnitOfWork(database.sessions))
    interval_seconds = settings.reference_cleanup_interval_hours * 60 * 60
    log_event(
        logger,
        logging.INFO,
        "reference_cleanup.worker_started",
        interval_hours=settings.reference_cleanup_interval_hours,
    )
    try:
        while True:
            try:
                await run_cleanup_cycle(service, now=datetime.now(UTC))
            except Exception:
                log_event(logger, logging.ERROR, "reference_cleanup.cycle_failed")
            await asyncio.sleep(interval_seconds)
    finally:
        await database.close()
        log_event(logger, logging.INFO, "reference_cleanup.worker_stopped")


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        asyncio.run(run_worker(settings))
    except RuntimeConfigurationError as exc:
        log_event(logger, logging.CRITICAL, "configuration.invalid", missing=exc.missing)
        raise SystemExit(2) from exc
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
