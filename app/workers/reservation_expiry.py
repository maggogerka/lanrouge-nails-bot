"""Restart-safe reservation expiry worker with transaction-local claim handling."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import RuntimeConfigurationError, Settings, get_settings
from app.database import Database
from app.domain.payments import aware_utc
from app.domain.reservations import (
    ReservationExpiryAction,
    ReservationExpiryResult,
)
from app.domain.tenancy import DEFAULT_BUSINESS_ID
from app.logging import configure_logging, log_event
from app.observability import ObservabilityConfigurationError, initialize_observability
from app.repositories.audit_repository import AuditRepository
from app.repositories.payment_repository import PaymentRepository
from app.repositories.reservation_repository import ReservationRepository
from app.runtime_health import (
    RESERVATION_EXPIRY_POLL_INTERVAL_SECONDS,
    RuntimeHeartbeat,
    open_component_heartbeat,
)
from app.services.reservation_service import ReservationService

logger = logging.getLogger(__name__)

RESERVATION_EXPIRY_BATCH_SIZE = 100


class ReservationExpiryWorkerCore:
    """Claim with SKIP LOCKED and isolate each mutation in a database savepoint."""

    def __init__(
        self,
        session: AsyncSession,
        reservations: ReservationRepository,
        service: ReservationService,
    ) -> None:
        self._session = session
        self._reservations = reservations
        self._service = service

    @classmethod
    def for_session(cls, session: AsyncSession, *, business_id: int) -> ReservationExpiryWorkerCore:
        reservations = ReservationRepository(session, business_id)
        service = ReservationService(
            reservations,
            PaymentRepository(session, business_id),
            AuditRepository(session, business_id),
        )
        return cls(session, reservations, service)

    async def run_cycle(
        self, *, now: datetime | None = None, limit: int = 100
    ) -> ReservationExpiryResult:
        """Run inside a caller-owned transaction and leave commit/rollback to it."""

        current = aware_utc(now)
        claimed = await self._reservations.claim_expired(now=current, limit=limit)
        expired = 0
        reconciled = 0
        errors = 0
        for reservation in claimed:
            try:
                async with self._session.begin_nested():
                    action = await self._service.expire_claimed(reservation, now=current)
                    await self._session.flush()
            except Exception:
                errors += 1
                continue
            if action is ReservationExpiryAction.EXPIRED:
                expired += 1
            else:
                reconciled += 1
        return ReservationExpiryResult(
            checked=len(claimed),
            expired=expired,
            reconciled_paid=reconciled,
            errors=errors,
        )


async def run_expiry_cycle(
    sessions: async_sessionmaker[AsyncSession],
    *,
    business_id: int = DEFAULT_BUSINESS_ID,
    now: datetime | None = None,
    limit: int = RESERVATION_EXPIRY_BATCH_SIZE,
) -> ReservationExpiryResult:
    """Commit one isolated tenant cycle after all row savepoints have completed."""

    async with sessions() as session, session.begin():
        worker = ReservationExpiryWorkerCore.for_session(session, business_id=business_id)
        return await worker.run_cycle(now=now, limit=limit)


async def run_worker(settings: Settings) -> None:
    """Validate the reservation runtime and supervise its component heartbeat."""

    settings.validate_reservation_worker_runtime()
    async with open_component_heartbeat(settings, "reservation_expiry") as heartbeat:
        await _run_worker(settings, heartbeat)


async def _run_worker(settings: Settings, heartbeat: RuntimeHeartbeat) -> None:
    database = Database.create(settings.database_url.get_secret_value())
    log_event(
        logger,
        logging.INFO,
        "reservation_expiry.worker_started",
        business_id=DEFAULT_BUSINESS_ID,
        batch_size=RESERVATION_EXPIRY_BATCH_SIZE,
        poll_interval_seconds=RESERVATION_EXPIRY_POLL_INTERVAL_SECONDS,
    )
    try:
        while True:
            result = await run_expiry_cycle(
                database.sessions,
                business_id=DEFAULT_BUSINESS_ID,
                now=datetime.now(UTC),
            )
            log_event(
                logger,
                logging.WARNING if result.errors else logging.INFO,
                "reservation_expiry.cycle_completed",
                checked=result.checked,
                expired=result.expired,
                reconciled_paid=result.reconciled_paid,
                error_count=result.errors,
            )
            if result.errors == 0:
                await heartbeat.beat()
            await asyncio.sleep(RESERVATION_EXPIRY_POLL_INTERVAL_SECONDS)
    finally:
        await database.close()
        log_event(logger, logging.INFO, "reservation_expiry.worker_stopped")


def run() -> None:
    """Load settings and run the reservation expiry process."""

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
