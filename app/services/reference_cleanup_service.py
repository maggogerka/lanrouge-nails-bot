"""Idempotent bounded cleanup of application-managed reference metadata."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from time import perf_counter

from app.domain.reference_retention import anonymize_reference
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.reference_cleanup import ReferenceCleanupHealth, ReferenceCleanupResult

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


class ReferenceCleanupService:
    """Anonymize expired Telegram IDs while preserving non-sensitive audit rows."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        *,
        batch_size: int = 1000,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._batch_size = batch_size

    async def run(
        self,
        *,
        dry_run: bool,
        now: datetime | None = None,
    ) -> ReferenceCleanupResult:
        current_time = self._aware_now(now)
        started = perf_counter()
        if not dry_run:
            await self._mark_started(current_time)
        try:
            async with self._unit_of_work_factory() as unit_of_work:
                candidates = await unit_of_work.reference_media.list_expired(
                    current_time,
                    limit=self._batch_size,
                )
                estimates = {
                    row.id: self._estimated_identifier_bytes(
                        row.telegram_file_id, row.telegram_file_unique_id
                    )
                    for row in candidates
                }
        except Exception:
            if not dry_run:
                await self._mark_finished(
                    current_time,
                    errors=1,
                    error_code="cleanup_candidate_query_error",
                )
            raise
        if dry_run:
            return ReferenceCleanupResult(
                checked=len(candidates),
                deleted=0,
                estimated_bytes_released=sum(estimates.values()),
                errors=0,
                duration_seconds=perf_counter() - started,
                dry_run=True,
            )

        deleted = 0
        released = 0
        errors = 0
        for media_id in estimates:
            try:
                item_released = await self._cleanup_one(media_id, current_time)
            except Exception:
                errors += 1
                await self._record_item_failure(media_id)
                continue
            if item_released is not None:
                deleted += 1
                released += item_released
        await self._mark_finished(current_time, errors=errors)
        return ReferenceCleanupResult(
            checked=len(candidates),
            deleted=deleted,
            estimated_bytes_released=released,
            errors=errors,
            duration_seconds=perf_counter() - started,
            dry_run=False,
        )

    async def cleanup_appointment_now(
        self,
        appointment_id: int,
        *,
        now: datetime | None = None,
    ) -> ReferenceCleanupResult:
        current_time = self._aware_now(now)
        started = perf_counter()
        async with self._unit_of_work_factory() as unit_of_work:
            rows = await unit_of_work.reference_media.list_active(appointment_id)
            released = sum(anonymize_reference(row, current_time) for row in rows)
            if rows:
                await unit_of_work.session.flush()
                await unit_of_work.commit()
        return ReferenceCleanupResult(
            checked=len(rows),
            deleted=len(rows),
            estimated_bytes_released=released,
            errors=0,
            duration_seconds=perf_counter() - started,
            dry_run=False,
        )

    async def health(self) -> ReferenceCleanupHealth:
        async with self._unit_of_work_factory() as unit_of_work:
            state = await unit_of_work.reference_media.get_cleanup_state()
            return ReferenceCleanupHealth(
                last_started_at=state.last_started_at,
                last_succeeded_at=state.last_succeeded_at,
                consecutive_failures=state.consecutive_failures,
                last_error_code=state.last_error_code,
            )

    async def _cleanup_one(self, media_id: int, now: datetime) -> int | None:
        async with self._unit_of_work_factory() as unit_of_work:
            row = await unit_of_work.reference_media.get(media_id, for_update=True)
            if row is None or row.deleted_at is not None or row.expires_at > now:
                return None
            released = anonymize_reference(row, now)
            await unit_of_work.session.flush()
            await unit_of_work.commit()
            return released

    async def _mark_started(self, now: datetime) -> None:
        async with self._unit_of_work_factory() as unit_of_work:
            state = await unit_of_work.reference_media.get_cleanup_state(for_update=True)
            state.last_started_at = now
            await unit_of_work.commit()

    async def _mark_finished(
        self,
        now: datetime,
        *,
        errors: int,
        error_code: str = "cleanup_item_errors",
    ) -> None:
        async with self._unit_of_work_factory() as unit_of_work:
            state = await unit_of_work.reference_media.get_cleanup_state(for_update=True)
            if errors:
                state.consecutive_failures += 1
                state.last_error_code = error_code
            else:
                state.last_succeeded_at = now
                state.consecutive_failures = 0
                state.last_error_code = None
            await unit_of_work.commit()

    async def _record_item_failure(self, media_id: int) -> None:
        try:
            async with self._unit_of_work_factory() as unit_of_work:
                row = await unit_of_work.reference_media.get(media_id, for_update=True)
                if row is not None and row.deleted_at is None:
                    row.deletion_attempts += 1
                    row.last_deletion_error = "temporary_cleanup_error"
                    await unit_of_work.commit()
        except Exception:
            return

    @staticmethod
    def _estimated_identifier_bytes(file_id: str | None, unique_id: str | None) -> int:
        return sum(len(value.encode()) for value in (file_id, unique_id) if value is not None)

    @staticmethod
    def _aware_now(value: datetime | None) -> datetime:
        current = value or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return current.astimezone(UTC)
