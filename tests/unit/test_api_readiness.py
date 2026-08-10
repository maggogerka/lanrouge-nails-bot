"""Boolean-only database and Redis API readiness checks."""

from __future__ import annotations

from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.readiness import ApiReadinessProbe, RedisPingClient


class FakeConnectionContext:
    def __init__(self, connection: MagicMock) -> None:
        self._connection = connection

    async def __aenter__(self) -> MagicMock:
        return self._connection

    async def __aexit__(self, *args: object) -> None:
        del args


class FakeEngine:
    def __init__(self, context: FakeConnectionContext) -> None:
        self._context = context

    def connect(self) -> FakeConnectionContext:
        return self._context


def engine_with_result(error: Exception | None = None) -> tuple[FakeEngine, MagicMock]:
    connection = MagicMock()
    connection.execute = AsyncMock(side_effect=error)
    engine = FakeEngine(FakeConnectionContext(connection))
    return engine, connection


@pytest.mark.asyncio
async def test_readiness_reports_only_boolean_dependency_state() -> None:
    engine, connection = engine_with_result()
    redis = MagicMock()
    redis.ping = AsyncMock(return_value=True)
    probe = ApiReadinessProbe(
        cast(AsyncEngine, engine),
        cast(RedisPingClient, redis),
    )

    report = await probe.check()

    assert report.ready
    assert report.checks == {"database": True, "redis": True}
    connection.execute.assert_awaited_once()
    redis.ping.assert_awaited_once()


@pytest.mark.asyncio
async def test_readiness_hides_dependency_exception_details() -> None:
    engine, _ = engine_with_result(OSError("postgresql://user:secret@example"))
    redis = MagicMock()
    redis.ping = AsyncMock(side_effect=OSError("redis://user:secret@example"))
    probe = ApiReadinessProbe(
        cast(AsyncEngine, engine),
        cast(RedisPingClient, redis),
    )

    report = await probe.check()

    assert not report.ready
    assert report.checks == {"database": False, "redis": False}
    assert "secret" not in repr(report)
