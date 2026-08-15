"""Redis server-time component heartbeat and overdue health tests."""

from __future__ import annotations

from collections.abc import Iterable

import pytest

from app.component_health import (
    ComponentHealthMonitor,
    ComponentPolicy,
    ComponentStatus,
)


class FakeRedis:
    def __init__(self, responses: Iterable[object]) -> None:
        self.responses = iter(responses)
        self.calls: list[tuple[str, int, tuple[str | int, ...]]] = []

    async def eval(self, script: str, numkeys: int, *keys_and_args: str | int) -> object:
        self.calls.append((script, numkeys, keys_and_args))
        result = next(self.responses)
        if isinstance(result, Exception):
            raise result
        return result


@pytest.mark.asyncio
async def test_heartbeat_uses_server_time_atomic_set_and_bounded_ttl() -> None:
    redis = FakeRedis([1_700_000_000_000])
    monitor = ComponentHealthMonitor(redis, (ComponentPolicy("reminders", 30),))

    timestamp = await monitor.heartbeat("reminders")

    assert timestamp.isoformat() == "2023-11-14T22:13:20+00:00"
    script, numkeys, args = redis.calls[0]
    assert numkeys == 1
    assert args == ("telegram_crm:heartbeat:reminders", 90_000)
    assert "redis.call('TIME')" in script
    assert "'PX'" in script


@pytest.mark.asyncio
async def test_heartbeat_key_is_isolated_by_deployment_instance() -> None:
    redis = FakeRedis([1_700_000_000_000])
    monitor = ComponentHealthMonitor(
        redis,
        (ComponentPolicy("reminders", 30),),
        namespace="studio",
        instance_id="instance-02",
    )

    await monitor.heartbeat("reminders")

    assert redis.calls[0][2][0] == "studio:instance-02:heartbeat:reminders"


@pytest.mark.asyncio
async def test_snapshot_distinguishes_healthy_overdue_and_optional_missing() -> None:
    now_ms = 1_700_000_000_000
    redis = FakeRedis([[now_ms, now_ms - 5_000, now_ms - 31_000, False]])
    failures: list[object] = []
    monitor = ComponentHealthMonitor(
        redis,
        (
            ComponentPolicy("bot", 10),
            ComponentPolicy("reminders", 30),
            ComponentPolicy("broadcasts", 60, required=False),
        ),
        failure_hook=failures.append,
    )

    snapshot = await monitor.check()

    assert not snapshot.healthy
    assert snapshot.store_available
    assert [item.status for item in snapshot.components] == [
        ComponentStatus.HEALTHY,
        ComponentStatus.OVERDUE,
        ComponentStatus.MISSING,
    ]
    assert snapshot.error_code == "component_overdue"
    assert failures == [snapshot]
    assert redis.calls[0][1] == 3


@pytest.mark.asyncio
async def test_store_outage_is_structured_unhealthy_and_hook_failure_is_isolated() -> None:
    redis = FakeRedis([OSError("redis://user:secret@example")])

    def broken_hook(_: object) -> None:
        raise RuntimeError("alert backend failed")

    monitor = ComponentHealthMonitor(
        redis,
        (ComponentPolicy("bot", 10),),
        failure_hook=broken_hook,
    )

    snapshot = await monitor.check()

    assert not snapshot.healthy
    assert not snapshot.store_available
    assert snapshot.error_code == "heartbeat_store_unavailable"
    assert snapshot.components[0].status is ComponentStatus.STORE_UNAVAILABLE
    assert "secret" not in str(snapshot.as_dict())


@pytest.mark.asyncio
async def test_unregistered_component_cannot_write_arbitrary_redis_key() -> None:
    redis = FakeRedis([])
    monitor = ComponentHealthMonitor(redis, (ComponentPolicy("bot", 10),))

    with pytest.raises(ValueError, match="not registered"):
        await monitor.heartbeat("../foreign")

    assert redis.calls == []


def test_policy_accepts_long_bounded_maintenance_intervals() -> None:
    policy = ComponentPolicy("reference_cleanup", 606_600)

    assert policy.max_age_seconds == 606_600
