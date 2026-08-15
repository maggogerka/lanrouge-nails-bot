"""Reservation expiry worker partitions claims and isolates item failures."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.models.commerce import BookingReservation
from app.domain.enums import ReservationStatus
from app.domain.reservations import ReservationExpiryAction, ReservationExpiryResult
from app.workers.reservation_expiry import ReservationExpiryWorkerCore, run_expiry_cycle

NOW = datetime(2026, 8, 10, 10, tzinfo=UTC)


def reservation(reservation_id: int) -> BookingReservation:
    return BookingReservation(
        id=reservation_id,
        business_id=7,
        client_id=41,
        staff_member_id=5,
        window_id=reservation_id,
        service_id=3,
        token_digest=f"{reservation_id:064x}",
        idempotency_key=f"request-{reservation_id:08d}",
        status=ReservationStatus.ACTIVE,
        expires_at=NOW - timedelta(seconds=1),
    )


@pytest.mark.asyncio
async def test_cycle_uses_savepoints_and_continues_after_one_failure() -> None:
    rows = [reservation(1), reservation(2), reservation(3)]
    repository = MagicMock()
    repository.claim_expired = AsyncMock(return_value=rows)
    service = MagicMock()
    service.expire_claimed = AsyncMock(
        side_effect=[
            ReservationExpiryAction.EXPIRED,
            RuntimeError("transient database failure"),
            ReservationExpiryAction.RECONCILED,
        ]
    )
    session = MagicMock()
    session.flush = AsyncMock()

    @asynccontextmanager
    async def savepoint() -> object:
        yield

    session.begin_nested.side_effect = savepoint
    worker = ReservationExpiryWorkerCore(session, repository, service)

    result = await worker.run_cycle(now=NOW, limit=20)

    repository.claim_expired.assert_awaited_once_with(now=NOW, limit=20)
    assert session.begin_nested.call_count == 3
    assert session.flush.await_count == 2
    assert result.checked == 3
    assert result.expired == 1
    assert result.reconciled_paid == 1
    assert result.errors == 1


@pytest.mark.asyncio
async def test_runtime_cycle_owns_outer_transaction_and_commits_core_result() -> None:
    expected = ReservationExpiryResult(
        checked=1,
        expired=1,
        reconciled_paid=0,
        errors=0,
    )
    session = MagicMock()
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=None)
    transaction.__aexit__ = AsyncMock(return_value=None)
    session.begin.return_value = transaction
    session_context = MagicMock()
    session_context.__aenter__ = AsyncMock(return_value=session)
    session_context.__aexit__ = AsyncMock(return_value=None)
    sessions = MagicMock(return_value=session_context)
    worker = MagicMock()
    worker.run_cycle = AsyncMock(return_value=expected)

    with pytest.MonkeyPatch.context() as monkeypatch:
        factory = MagicMock(return_value=worker)
        monkeypatch.setattr(ReservationExpiryWorkerCore, "for_session", factory)
        result = await run_expiry_cycle(sessions, business_id=7, now=NOW, limit=20)

    assert result is expected
    factory.assert_called_once_with(session, business_id=7)
    worker.run_cycle.assert_awaited_once_with(now=NOW, limit=20)
    transaction.__aenter__.assert_awaited_once()
    transaction.__aexit__.assert_awaited_once_with(None, None, None)
    session_context.__aexit__.assert_awaited_once_with(None, None, None)
