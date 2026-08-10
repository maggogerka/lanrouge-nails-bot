"""Dependency and service-specific heartbeat healthcheck tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.config import RuntimeConfigurationError, Settings
from app.healthcheck import check_runtime_health, run
from app.runtime_health import ComponentUnhealthyError


def settings() -> Settings:
    return Settings(
        _env_file=None,
        DATABASE_URL="postgresql+asyncpg://user:password@localhost/database",
        REDIS_URL="redis://localhost:6379/0",
    )


@pytest.mark.asyncio
async def test_dependency_only_healthcheck_preserves_legacy_mode() -> None:
    dependencies = AsyncMock()
    component = AsyncMock()

    with (
        patch("app.healthcheck.check_dependencies", new=dependencies),
        patch("app.healthcheck.check_component_heartbeat", new=component),
    ):
        await check_runtime_health(settings())

    dependencies.assert_awaited_once()
    component.assert_not_awaited()


@pytest.mark.asyncio
async def test_component_healthcheck_checks_dependencies_then_only_selected_component() -> None:
    calls: list[str] = []

    async def dependencies(_: Settings) -> None:
        calls.append("dependencies")

    async def component(_: Settings, name: str) -> None:
        calls.append(name)

    with (
        patch("app.healthcheck.check_dependencies", side_effect=dependencies),
        patch("app.healthcheck.check_component_heartbeat", side_effect=component),
    ):
        await check_runtime_health(settings(), component="reminders")

    assert calls == ["dependencies", "reminders"]


def test_component_failure_exits_unhealthy_without_exposing_exception() -> None:
    with (
        patch(
            "app.healthcheck._main",
            new=AsyncMock(
                side_effect=ComponentUnhealthyError("bot", "overdue", "component_overdue")
            ),
        ),
        pytest.raises(SystemExit) as caught,
    ):
        run(["--component", "bot"])

    assert caught.value.code == 1


def test_runtime_configuration_failure_has_distinct_exit_code() -> None:
    with (
        patch(
            "app.healthcheck._main",
            new=AsyncMock(side_effect=RuntimeConfigurationError(("REDIS_URL",))),
        ),
        pytest.raises(SystemExit) as caught,
    ):
        run(["--component", "reference_cleanup"])

    assert caught.value.code == 2


def test_unknown_component_is_cli_usage_error() -> None:
    with pytest.raises(SystemExit) as caught:
        run(["--component", "unknown"])

    assert caught.value.code == 2
