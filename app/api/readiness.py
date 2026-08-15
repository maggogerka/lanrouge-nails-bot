"""Bounded boolean readiness projection for the API process."""

from __future__ import annotations

import asyncio
from typing import Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.api.contracts import ReadinessReport


class RedisPingClient(Protocol):
    async def ping(self) -> object: ...


class ApiReadinessProbe:
    """Check only dependency availability; exception details stay server-side."""

    def __init__(self, engine: AsyncEngine, redis: RedisPingClient) -> None:
        self._engine = engine
        self._redis = redis

    async def check(self) -> ReadinessReport:
        database, redis = await asyncio.gather(
            self._database_ready(),
            self._redis_ready(),
        )
        checks = {"database": database, "redis": redis}
        return ReadinessReport(ready=all(checks.values()), checks=checks)

    async def _database_ready(self) -> bool:
        try:
            async with self._engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except Exception:
            return False
        return True

    async def _redis_ready(self) -> bool:
        try:
            return await self._redis.ping() is True
        except Exception:
            return False
