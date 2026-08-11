"""Instance-scoped runtime heartbeat composition tests."""

from __future__ import annotations

import logging
from collections.abc import Iterable
from unittest.mock import patch

import pytest

from app.component_health import ComponentHealthMonitor, ComponentPolicy
from app.config import Settings
from app.runtime_health import (
    ComponentUnhealthyError,
    RuntimeHeartbeat,
    check_component_heartbeat,
    component_policy,
    open_component_heartbeat,
)


class FakeRedis:
    def __init__(self, responses: Iterable[object]) -> None:
        self.responses = iter(responses)
        self.calls: list[tuple[int, tuple[str | int, ...]]] = []
        self.closed = False

    async def eval(self, script: str, numkeys: int, *keys_and_args: str | int) -> object:
        del script
        self.calls.append((numkeys, keys_and_args))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response

    async def aclose(self) -> None:
        self.closed = True


def settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "DATABASE_URL": "postgresql+asyncpg://user:password@localhost/database",
        "REDIS_URL": "redis://localhost:6379/0",
        "REDIS_NAMESPACE": "studio",
        "INSTANCE_ID": "instance-02",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.mark.asyncio
async def test_open_heartbeat_uses_namespace_and_instance_and_closes_client() -> None:
    redis = FakeRedis([1_700_000_000_000])

    with patch("app.runtime_health._redis_client", return_value=redis):
        async with open_component_heartbeat(settings(), "reminders") as heartbeat:
            assert await heartbeat.beat()

    assert redis.calls[0][1][0] == "studio:instance-02:heartbeat:reminders"
    assert redis.closed


@pytest.mark.asyncio
async def test_heartbeat_store_failure_is_nonfatal_and_logs_only_safe_code(
    caplog: pytest.LogCaptureFixture,
) -> None:
    redis = FakeRedis([OSError("redis://user:private@example.test/0")])
    monitor = ComponentHealthMonitor(
        redis,
        (ComponentPolicy("bot", 60),),
        namespace="studio",
        instance_id="instance-02",
    )
    heartbeat = RuntimeHeartbeat(redis, monitor, "bot")  # type: ignore[arg-type]

    with caplog.at_level(logging.ERROR):
        written = await heartbeat.beat()

    assert not written
    assert caplog.records[0].error_code == "heartbeat_store_unavailable"
    assert "private" not in caplog.text


def test_component_policies_follow_worker_intervals_at_max_cleanup_setting() -> None:
    configured = settings(
        REMINDER_POLL_INTERVAL_SECONDS=7.5,
        REFERENCE_CLEANUP_INTERVAL_HOURS=168,
    )

    assert component_policy(configured, "bot").max_age_seconds == 60
    assert component_policy(configured, "reminders").max_age_seconds == 45
    assert component_policy(configured, "broadcasts").max_age_seconds == 45
    assert component_policy(configured, "reference_cleanup").max_age_seconds == 606_600
    assert component_policy(configured, "reservation_expiry").max_age_seconds == 40
    assert component_policy(configured, "privacy_deletion").max_age_seconds == 150


@pytest.mark.asyncio
async def test_component_check_reads_only_selected_key_and_closes_client() -> None:
    now_ms = 1_700_000_000_000
    redis = FakeRedis([[now_ms, now_ms - 1_000]])

    with patch("app.runtime_health._redis_client", return_value=redis):
        snapshot = await check_component_heartbeat(settings(), "bot")

    assert snapshot.healthy
    assert redis.calls[0][0] == 1
    assert redis.calls[0][1] == ("studio:instance-02:heartbeat:bot",)
    assert redis.closed


@pytest.mark.asyncio
async def test_component_check_maps_missing_heartbeat_to_safe_failure_and_closes() -> None:
    redis = FakeRedis([[1_700_000_000_000, False]])

    with (
        patch("app.runtime_health._redis_client", return_value=redis),
        pytest.raises(ComponentUnhealthyError) as caught,
    ):
        await check_component_heartbeat(settings(), "bot")

    assert caught.value.component == "bot"
    assert caught.value.status == "missing"
    assert caught.value.error_code == "component_overdue"
    assert redis.closed
