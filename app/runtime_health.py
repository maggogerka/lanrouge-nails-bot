"""Runtime wiring for instance-scoped component heartbeats."""

from __future__ import annotations

import asyncio
import logging
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from redis.asyncio import Redis

from app.component_health import (
    ComponentHealthMonitor,
    ComponentHealthSnapshot,
    ComponentPolicy,
)
from app.config import Settings
from app.logging import log_event

logger = logging.getLogger(__name__)

BOT_HEARTBEAT_INTERVAL_SECONDS = 20.0
RESERVATION_EXPIRY_POLL_INTERVAL_SECONDS = 5.0
PRIVACY_DELETION_POLL_INTERVAL_SECONDS = 60.0

_COMPONENTS = frozenset(
    {
        "bot",
        "reminders",
        "broadcasts",
        "reference_cleanup",
        "reservation_expiry",
        "privacy_deletion",
    }
)


class ComponentUnhealthyError(RuntimeError):
    """A required component heartbeat is missing, overdue, or unavailable."""

    def __init__(self, component: str, status: str, error_code: str) -> None:
        self.component = component
        self.status = status
        self.error_code = error_code
        super().__init__("component healthcheck failed")


class RuntimeHeartbeat:
    """Publish safe best-effort heartbeats and own the dedicated Redis client."""

    def __init__(
        self,
        redis: Redis,
        monitor: ComponentHealthMonitor,
        component: str,
    ) -> None:
        self._redis = redis
        self._monitor = monitor
        self.component = component

    async def beat(self) -> bool:
        """Record successful work; Redis failure must not cancel useful work."""

        try:
            await self._monitor.heartbeat(self.component)
        except Exception:
            log_event(
                logger,
                logging.ERROR,
                "component.heartbeat_failed",
                component=self.component,
                error_code="heartbeat_store_unavailable",
            )
            return False
        return True

    async def run_periodically(self, *, interval_seconds: float) -> None:
        """Publish bot liveness until explicitly cancelled by its owner."""

        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        while True:
            await asyncio.sleep(interval_seconds)
            await self.beat()

    async def close(self) -> None:
        await self._redis.aclose()


def runtime_component_names() -> tuple[str, ...]:
    """Return stable CLI choices without exposing a mutable registry."""

    return tuple(sorted(_COMPONENTS))


def component_policy(settings: Settings, component: str) -> ComponentPolicy:
    """Derive the documented policy from validated process configuration."""

    if component not in _COMPONENTS:
        raise ValueError("unknown runtime component")
    if component == "bot":
        max_age_seconds = 60
    elif component in {"reminders", "broadcasts"}:
        max_age_seconds = math.ceil(2 * settings.reminder_poll_interval_seconds + 30)
    elif component == "reference_cleanup":
        max_age_seconds = settings.reference_cleanup_interval_hours * 3600 + 1800
    elif component == "privacy_deletion":
        max_age_seconds = math.ceil(2 * PRIVACY_DELETION_POLL_INTERVAL_SECONDS + 30)
    else:
        max_age_seconds = math.ceil(2 * RESERVATION_EXPIRY_POLL_INTERVAL_SECONDS + 30)
    return ComponentPolicy(component, max_age_seconds)


def component_monitor(
    settings: Settings,
    redis: Redis,
    *,
    component: str,
) -> ComponentHealthMonitor:
    """Build the same monitor shape for publishers and health probes."""

    return ComponentHealthMonitor(
        redis,
        (component_policy(settings, component),),
        namespace=settings.redis_namespace,
        instance_id=settings.instance_id,
    )


@asynccontextmanager
async def open_component_heartbeat(
    settings: Settings,
    component: str,
) -> AsyncIterator[RuntimeHeartbeat]:
    """Open and always close a component's independent Redis connection."""

    redis = _redis_client(settings)
    heartbeat = RuntimeHeartbeat(
        redis,
        component_monitor(settings, redis, component=component),
        component,
    )
    try:
        yield heartbeat
    finally:
        await heartbeat.close()


async def check_component_heartbeat(
    settings: Settings,
    component: str,
) -> ComponentHealthSnapshot:
    """Read one component heartbeat and raise a non-sensitive readiness error."""

    redis = _redis_client(settings)
    try:
        snapshot = await component_monitor(settings, redis, component=component).check()
    finally:
        await redis.aclose()
    if not snapshot.healthy:
        state = snapshot.components[0]
        raise ComponentUnhealthyError(
            component,
            state.status.value,
            snapshot.error_code or "component_unhealthy",
        )
    return snapshot


def _redis_client(settings: Settings) -> Redis:
    return Redis.from_url(
        settings.redis_url.get_secret_value(),
        socket_connect_timeout=5,
        socket_timeout=5,
    )
