"""Worker heartbeat ordering and cleanup tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config import Settings
from app.domain.reservations import ReservationExpiryResult
from app.schemas.reference_cleanup import ReferenceCleanupResult
from app.workers import broadcasts, reference_cleanup, reminders, reservation_expiry


def settings() -> Settings:
    return Settings(
        _env_file=None,
        BOT_TOKEN="123456:development-token",
        DATABASE_URL="postgresql+asyncpg://user:password@localhost/database",
        REDIS_URL="redis://localhost:6379/0",
    )


def bot_context() -> MagicMock:
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=MagicMock())
    context.__aexit__ = AsyncMock(return_value=None)
    return context


@pytest.mark.asyncio
async def test_reminder_empty_success_beats_then_cancellation_closes_database() -> None:
    database = MagicMock()
    database.close = AsyncMock()
    heartbeat = MagicMock()
    heartbeat.beat = AsyncMock()

    with (
        patch("app.workers.reminders.Database.create", return_value=database),
        patch("app.workers.reminders.Bot", return_value=bot_context()),
        patch("app.workers.reminders.run_delivery_cycle", new=AsyncMock(return_value=0)),
        patch(
            "app.workers.reminders.asyncio.sleep",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await reminders._run_worker(settings(), heartbeat)

    heartbeat.beat.assert_awaited_once()
    database.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_reminder_failed_cycle_never_beats_and_closes_database() -> None:
    database = MagicMock()
    database.close = AsyncMock()
    heartbeat = MagicMock()
    heartbeat.beat = AsyncMock()

    with (
        patch("app.workers.reminders.Database.create", return_value=database),
        patch("app.workers.reminders.Bot", return_value=bot_context()),
        patch(
            "app.workers.reminders.run_delivery_cycle",
            new=AsyncMock(side_effect=RuntimeError("database unavailable")),
        ),
        pytest.raises(RuntimeError, match="database unavailable"),
    ):
        await reminders._run_worker(settings(), heartbeat)

    heartbeat.beat.assert_not_awaited()
    database.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_broadcast_failed_cycle_never_beats_and_closes_database() -> None:
    database = MagicMock()
    database.close = AsyncMock()
    heartbeat = MagicMock()
    heartbeat.beat = AsyncMock()
    unit_of_work = MagicMock()
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    unit_of_work.settings.get = AsyncMock(
        return_value=SimpleNamespace(
            broadcast_max_retries=3,
            broadcast_messages_per_second=10,
        )
    )

    with (
        patch("app.workers.broadcasts.Database.create", return_value=database),
        patch("app.workers.broadcasts.SqlAlchemyUnitOfWork", return_value=unit_of_work),
        patch("app.workers.broadcasts.Bot", return_value=bot_context()),
        patch(
            "app.workers.broadcasts.run_delivery_cycle",
            new=AsyncMock(side_effect=RuntimeError("claim failed")),
        ),
        pytest.raises(RuntimeError, match="claim failed"),
    ):
        await broadcasts._run_worker(settings(), heartbeat)

    heartbeat.beat.assert_not_awaited()
    database.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("errors, expected_beats", [(0, 1), (1, 0)])
async def test_reference_cleanup_beats_only_after_error_free_result(
    errors: int, expected_beats: int
) -> None:
    database = MagicMock()
    database.close = AsyncMock()
    heartbeat = MagicMock()
    heartbeat.beat = AsyncMock()
    result = ReferenceCleanupResult(
        checked=2,
        deleted=1,
        estimated_bytes_released=10,
        errors=errors,
        duration_seconds=0.5,
        dry_run=False,
    )

    with (
        patch("app.workers.reference_cleanup.Database.create", return_value=database),
        patch(
            "app.workers.reference_cleanup.run_cleanup_cycle",
            new=AsyncMock(return_value=result),
        ),
        patch(
            "app.workers.reference_cleanup.asyncio.sleep",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await reference_cleanup._run_worker(settings(), heartbeat)

    assert heartbeat.beat.await_count == expected_beats
    database.close.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("errors, expected_beats", [(0, 1), (1, 0)])
async def test_reservation_worker_beats_only_after_committed_error_free_cycle(
    errors: int, expected_beats: int
) -> None:
    database = MagicMock()
    database.close = AsyncMock()
    heartbeat = MagicMock()
    heartbeat.beat = AsyncMock()
    result = ReservationExpiryResult(
        checked=2,
        expired=1,
        reconciled_paid=0,
        errors=errors,
    )

    with (
        patch("app.workers.reservation_expiry.Database.create", return_value=database),
        patch(
            "app.workers.reservation_expiry.run_expiry_cycle",
            new=AsyncMock(return_value=result),
        ),
        patch(
            "app.workers.reservation_expiry.asyncio.sleep",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await reservation_expiry._run_worker(settings(), heartbeat)

    assert heartbeat.beat.await_count == expected_beats
    database.close.assert_awaited_once()
