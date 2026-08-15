"""Bounded worker for approved and stale privacy anonymization requests."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from app.config import RuntimeConfigurationError, Settings, get_settings
from app.database import Database
from app.domain.enums import DataDeletionRequestStatus
from app.logging import configure_logging, log_event
from app.observability import ObservabilityConfigurationError, initialize_observability
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.runtime_health import PRIVACY_DELETION_POLL_INTERVAL_SECONDS, open_component_heartbeat
from app.schemas.service import AdminActor
from app.services.authorization_service import AuthorizationService
from app.services.privacy_service import PrivacyDeletionRuntimeService

logger = logging.getLogger(__name__)


async def run_deletion_cycle(
    service: PrivacyDeletionRuntimeService,
    actor: AdminActor,
    *,
    now: datetime,
) -> tuple[int, int]:
    """Process a bounded page; row locks and leases make concurrent workers harmless."""

    requests = await service.list_requests(
        actor,
        statuses=(
            DataDeletionRequestStatus.APPROVED,
            DataDeletionRequestStatus.PROCESSING,
        ),
        limit=50,
    )
    completed = 0
    stopped = 0
    for request in requests:
        outcome = await service.execute_anonymization(
            actor,
            request.id,
            confirmed=True,
            correlation_id=f"privacy-worker-{request.id}",
            now=now,
        )
        if outcome.completed:
            completed += 1
        else:
            stopped += 1
    return completed, stopped


async def run_worker(settings: Settings) -> None:
    settings.validate_dependency_runtime()
    owner_ids = settings.configured_owner_telegram_ids
    if not owner_ids:
        raise RuntimeConfigurationError(("ADMIN_TELEGRAM_IDS",))
    database = Database.create(settings.database_url.get_secret_value())
    actor = AdminActor(telegram_id=owner_ids[0])
    service = PrivacyDeletionRuntimeService(
        lambda: SqlAlchemyUnitOfWork(database.sessions),
        AuthorizationService(database.sessions),
    )
    try:
        async with open_component_heartbeat(settings, "privacy_deletion") as heartbeat:
            while True:
                try:
                    completed, stopped = await run_deletion_cycle(
                        service, actor, now=datetime.now(UTC)
                    )
                except Exception:
                    logger.exception(
                        "privacy_deletion.cycle_failed",
                        extra={"event": "privacy_deletion.cycle_failed"},
                    )
                else:
                    log_event(
                        logger,
                        logging.INFO,
                        "privacy_deletion.cycle_completed",
                        completed=completed,
                        stopped=stopped,
                    )
                    await heartbeat.beat()
                await asyncio.sleep(PRIVACY_DELETION_POLL_INTERVAL_SECONDS)
    finally:
        await database.close()


def run() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        initialize_observability(settings)
        asyncio.run(run_worker(settings))
    except RuntimeConfigurationError as exc:
        log_event(logger, logging.CRITICAL, "configuration.invalid", missing=exc.missing)
        raise SystemExit(2) from exc
    except ObservabilityConfigurationError as exc:
        log_event(
            logger,
            logging.CRITICAL,
            "observability.configuration_invalid",
            error_code="sentry_initialization_failed",
        )
        raise SystemExit(2) from exc
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    run()
