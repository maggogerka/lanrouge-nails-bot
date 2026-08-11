"""Bounded privacy worker orchestration tests."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.schemas.service import AdminActor
from app.workers.privacy_deletion import run_deletion_cycle

NOW = datetime(2026, 8, 11, 12, tzinfo=UTC)


@pytest.mark.asyncio
async def test_worker_processes_bounded_requests_and_surfaces_stops() -> None:
    service = MagicMock()
    service.list_requests = AsyncMock(return_value=(SimpleNamespace(id=1), SimpleNamespace(id=2)))
    service.execute_anonymization = AsyncMock(
        side_effect=(
            SimpleNamespace(completed=True),
            SimpleNamespace(completed=False),
        )
    )
    actor = AdminActor(telegram_id=700)

    result = await run_deletion_cycle(service, actor, now=NOW)

    assert result == (1, 1)
    assert service.execute_anonymization.await_count == 2
    assert all(call.kwargs["confirmed"] for call in service.execute_anonymization.await_args_list)
