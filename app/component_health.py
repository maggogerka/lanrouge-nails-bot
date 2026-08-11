"""Redis-backed component heartbeats and overdue health snapshots."""

from __future__ import annotations

import re
from collections.abc import Awaitable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

_SAFE_COMPONENT = re.compile(r"^[a-z][a-z0-9_-]{1,47}$")
_SAFE_INSTANCE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")

_HEARTBEAT_SCRIPT = """
local now_parts = redis.call('TIME')
local now_ms = (tonumber(now_parts[1]) * 1000) + math.floor(tonumber(now_parts[2]) / 1000)
redis.call('SET', KEYS[1], tostring(now_ms), 'PX', ARGV[1])
return now_ms
"""

_SNAPSHOT_SCRIPT = """
local now_parts = redis.call('TIME')
local now_ms = (tonumber(now_parts[1]) * 1000) + math.floor(tonumber(now_parts[2]) / 1000)
local values = redis.call('MGET', unpack(KEYS))
local result = {now_ms}
for _, value in ipairs(values) do
    table.insert(result, value or false)
end
return result
"""


class HeartbeatRedis(Protocol):
    def eval(self, script: str, numkeys: int, *keys_and_args: str | int) -> Awaitable[object]: ...


class ComponentStatus(StrEnum):
    HEALTHY = "healthy"
    OVERDUE = "overdue"
    MISSING = "missing"
    STORE_UNAVAILABLE = "store_unavailable"


@dataclass(frozen=True, slots=True)
class ComponentPolicy:
    name: str
    max_age_seconds: int
    required: bool = True

    def __post_init__(self) -> None:
        if not _SAFE_COMPONENT.fullmatch(self.name):
            raise ValueError("component name must be a safe lowercase identifier")
        if not 1 <= self.max_age_seconds <= 2_678_400:
            raise ValueError("max_age_seconds must be between 1 and 2678400")


@dataclass(frozen=True, slots=True)
class ComponentState:
    name: str
    status: ComponentStatus
    required: bool
    max_age_seconds: int
    age_seconds: float | None
    last_heartbeat_at: datetime | None


@dataclass(frozen=True, slots=True)
class ComponentHealthSnapshot:
    healthy: bool
    store_available: bool
    checked_at: datetime
    components: tuple[ComponentState, ...]
    error_code: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "healthy": self.healthy,
            "store_available": self.store_available,
            "checked_at": self.checked_at.isoformat(),
            "error_code": self.error_code,
            "components": [
                {
                    "name": item.name,
                    "status": item.status.value,
                    "required": item.required,
                    "max_age_seconds": item.max_age_seconds,
                    "age_seconds": item.age_seconds,
                    "last_heartbeat_at": (
                        item.last_heartbeat_at.isoformat()
                        if item.last_heartbeat_at is not None
                        else None
                    ),
                }
                for item in self.components
            ],
        }


class ComponentFailureHook(Protocol):
    def __call__(self, snapshot: ComponentHealthSnapshot) -> None: ...


class ComponentHealthMonitor:
    """Use Redis server time so every process shares the same heartbeat clock."""

    def __init__(
        self,
        redis: HeartbeatRedis,
        policies: tuple[ComponentPolicy, ...],
        *,
        namespace: str = "telegram_crm",
        instance_id: str | None = None,
        failure_hook: ComponentFailureHook | None = None,
    ) -> None:
        if not _SAFE_COMPONENT.fullmatch(namespace):
            raise ValueError("namespace must be a safe lowercase identifier")
        if not policies:
            raise ValueError("at least one component policy is required")
        if instance_id is not None and not _SAFE_INSTANCE.fullmatch(instance_id):
            raise ValueError("instance_id must be a safe lowercase identifier")
        names = [policy.name for policy in policies]
        if len(names) != len(set(names)):
            raise ValueError("component policies must be unique")
        self._redis = redis
        self._policies = policies
        self._by_name = {policy.name: policy for policy in policies}
        self._namespace = namespace
        self._instance_id = instance_id
        self._failure_hook = failure_hook

    async def heartbeat(self, component: str) -> datetime:
        policy = self._by_name.get(component)
        if policy is None:
            raise ValueError("component is not registered")
        retention_ms = max(policy.max_age_seconds * 3, 60) * 1000
        try:
            raw = await self._redis.eval(
                _HEARTBEAT_SCRIPT,
                1,
                self._key(component),
                retention_ms,
            )
            epoch_ms = _integer(raw)
        except Exception as exc:
            raise RuntimeError("heartbeat store unavailable") from exc
        return datetime.fromtimestamp(epoch_ms / 1000, tz=UTC)

    async def check(self) -> ComponentHealthSnapshot:
        keys = tuple(self._key(policy.name) for policy in self._policies)
        try:
            raw = await self._redis.eval(_SNAPSHOT_SCRIPT, len(keys), *keys)
            now_ms, heartbeat_values = _snapshot_values(raw, len(keys))
        except Exception:
            snapshot = self._unavailable_snapshot()
            self._notify_failure(snapshot)
            return snapshot

        states = tuple(
            self._state(policy, heartbeat, now_ms)
            for policy, heartbeat in zip(self._policies, heartbeat_values, strict=True)
        )
        healthy = all(
            not state.required or state.status is ComponentStatus.HEALTHY for state in states
        )
        snapshot = ComponentHealthSnapshot(
            healthy=healthy,
            store_available=True,
            checked_at=datetime.fromtimestamp(now_ms / 1000, tz=UTC),
            components=states,
            error_code=None if healthy else "component_overdue",
        )
        if not healthy:
            self._notify_failure(snapshot)
        return snapshot

    def _state(
        self, policy: ComponentPolicy, heartbeat_ms: int | None, now_ms: int
    ) -> ComponentState:
        if heartbeat_ms is None:
            return ComponentState(
                name=policy.name,
                status=ComponentStatus.MISSING,
                required=policy.required,
                max_age_seconds=policy.max_age_seconds,
                age_seconds=None,
                last_heartbeat_at=None,
            )
        age = max(0.0, (now_ms - heartbeat_ms) / 1000)
        status = (
            ComponentStatus.HEALTHY if age <= policy.max_age_seconds else ComponentStatus.OVERDUE
        )
        return ComponentState(
            name=policy.name,
            status=status,
            required=policy.required,
            max_age_seconds=policy.max_age_seconds,
            age_seconds=age,
            last_heartbeat_at=datetime.fromtimestamp(heartbeat_ms / 1000, tz=UTC),
        )

    def _unavailable_snapshot(self) -> ComponentHealthSnapshot:
        return ComponentHealthSnapshot(
            healthy=False,
            store_available=False,
            checked_at=datetime.now(UTC),
            components=tuple(
                ComponentState(
                    name=policy.name,
                    status=ComponentStatus.STORE_UNAVAILABLE,
                    required=policy.required,
                    max_age_seconds=policy.max_age_seconds,
                    age_seconds=None,
                    last_heartbeat_at=None,
                )
                for policy in self._policies
            ),
            error_code="heartbeat_store_unavailable",
        )

    def _notify_failure(self, snapshot: ComponentHealthSnapshot) -> None:
        if self._failure_hook is None:
            return
        try:
            self._failure_hook(snapshot)
        except Exception:
            return

    def _key(self, component: str) -> str:
        instance_segment = f":{self._instance_id}" if self._instance_id is not None else ""
        return f"{self._namespace}{instance_segment}:heartbeat:{component}"


def _integer(value: object) -> int:
    if isinstance(value, bool):
        raise ValueError("boolean is not a timestamp")
    if isinstance(value, (bytes, str, int)):
        parsed = int(value)
        if parsed > 0:
            return parsed
    raise ValueError("invalid heartbeat timestamp")


def _snapshot_values(raw: object, expected: int) -> tuple[int, tuple[int | None, ...]]:
    if not isinstance(raw, (list, tuple)) or len(raw) != expected + 1:
        raise ValueError("invalid heartbeat snapshot")
    now_ms = _integer(raw[0])
    heartbeats: list[int | None] = []
    for value in raw[1:]:
        heartbeats.append(None if value is None or value is False else _integer(value))
    return now_ms, tuple(heartbeats)
