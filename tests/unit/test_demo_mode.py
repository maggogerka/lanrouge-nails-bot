"""Public demo safety, configuration and immutable-catalogue contracts."""

from __future__ import annotations

import ast
import inspect
from hashlib import sha256
from pathlib import Path

import pytest

from app.config import AppMode, RuntimeConfigurationError, Settings
from app.demo.composition import create_demo_dispatcher
from app.demo.keyboards import admin_menu, client_menu, main_menu, master_menu
from app.demo.policy import DemoActionBlocked, DemoOperation, DemoPolicy
from app.demo.service import DemoForm, DemoScreen, DemoService


def demo_settings(**overrides: str) -> Settings:
    values = {
        "APP_MODE": "demo",
        "BOT_TOKEN": "123456:separate-demo-token",
        "DATABASE_URL": "",
        "REDIS_URL": "redis://localhost:6379/1",
        "PRODUCTION_BOT_TOKEN_SHA256": sha256(b"production-token").hexdigest(),
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_production_is_the_default_mode() -> None:
    settings = Settings(_env_file=None)  # type: ignore[call-arg]

    assert settings.app_mode is AppMode.PRODUCTION


def test_demo_requires_only_redis_token_and_production_token_fingerprint() -> None:
    settings = demo_settings(PRODUCTION_BOT_TOKEN_SHA256="")

    with pytest.raises(RuntimeConfigurationError) as error:
        settings.validate_bot_runtime()

    assert error.value.missing == ("PRODUCTION_BOT_TOKEN_SHA256",)


def test_demo_rejects_reused_token_without_rendering_secret() -> None:
    token = "123456:separate-demo-token"
    settings = demo_settings(PRODUCTION_BOT_TOKEN_SHA256=sha256(token.encode()).hexdigest())

    with pytest.raises(ValueError, match="must differ") as error:
        settings.validate_bot_runtime()

    assert token not in str(error.value)


def test_demo_forbids_every_database_url() -> None:
    settings = demo_settings(DATABASE_URL="postgresql+asyncpg://demo:secret@localhost/crm_demo")

    with pytest.raises(ValueError, match="forbidden") as error:
        settings.validate_bot_runtime()

    assert "secret" not in str(error.value)


@pytest.mark.parametrize(
    "validator",
    [
        "validate_dependency_runtime",
        "validate_database_runtime",
        "validate_worker_runtime",
        "validate_api_runtime",
        "validate_yookassa_runtime",
        "validate_reservation_worker_runtime",
    ],
)
def test_demo_forbids_every_production_component(validator: str) -> None:
    settings = demo_settings()

    with pytest.raises(ValueError, match="forbidden in demo mode"):
        getattr(settings, validator)()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("ADMIN_TELEGRAM_IDS", "100"),
        ("SENTRY_DSN", "https://key@example.com/1"),
        ("YOOKASSA_SHOP_ID", "shop"),
        ("MINI_APP_ALLOWED_ORIGINS", "https://example.com"),
    ],
)
def test_demo_forbids_production_integrations(name: str, value: str) -> None:
    with pytest.raises(ValueError):
        demo_settings(**{name: value}).validate_bot_runtime()


def test_demo_policy_blocks_every_business_side_effect() -> None:
    policy = DemoPolicy()
    allowed = {
        DemoOperation.READ,
        DemoOperation.NAVIGATE,
        DemoOperation.TRANSIENT_STATE,
    }

    for operation in DemoOperation:
        if operation in allowed:
            policy.require(operation)
        else:
            with pytest.raises(DemoActionBlocked):
                policy.require(operation)


def test_every_form_finishes_at_a_blocked_operation() -> None:
    service = DemoService(demo_settings().timezone_info)

    for form_id in DemoForm:
        spec = service.form(form_id)
        assert spec.steps
        with pytest.raises(DemoActionBlocked):
            service.reject(spec.operation)


def test_demo_catalogue_is_fictional_and_read_only() -> None:
    service = DemoService(demo_settings().timezone_info)

    first = service.entities(DemoScreen.SERVICES)
    second = service.entities(DemoScreen.SERVICES)

    assert first is second
    assert first
    with pytest.raises((AttributeError, TypeError)):
        first[0].title = "changed"  # type: ignore[misc]


def test_demo_runtime_composition_has_no_database_parameter() -> None:
    signature = inspect.signature(create_demo_dispatcher)

    assert tuple(signature.parameters) == ("settings",)


def test_demo_package_has_no_persistence_imports_or_calls() -> None:
    root = Path("app/demo")
    forbidden_imports = ("app.database", "app.repositories", "sqlalchemy")
    forbidden_calls = {"add", "commit", "delete", "execute", "flush", "merge"}

    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith(forbidden_imports), path
            if isinstance(node, ast.Import):
                assert all(not alias.name.startswith(forbidden_imports) for alias in node.names), (
                    path
                )
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden_calls, (path, node.func.attr)


def test_public_menus_are_bounded_and_callback_payloads_fit_telegram() -> None:
    markups = (main_menu(), client_menu(), admin_menu(0), admin_menu(1), master_menu())

    for markup in markups:
        buttons = [button for row in markup.inline_keyboard for button in row]
        assert len(buttons) <= 20
        assert all(
            button.callback_data is None or len(button.callback_data.encode("utf-8")) <= 64
            for button in buttons
        )
