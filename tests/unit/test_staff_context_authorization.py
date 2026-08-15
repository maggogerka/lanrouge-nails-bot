"""Runtime staff-context authorization boundary tests."""

from __future__ import annotations

from typing import Any

import pytest
from aiogram.types import TelegramObject
from pydantic import ValidationError

from app.domain.enums import StaffRole
from app.domain.errors import AuthorizationError
from app.middlewares.staff_context import RuntimeAuthorizationMiddleware, StaffContextMiddleware
from app.schemas.authorization import StaffContext
from app.schemas.service import AdminActor
from app.security import get_staff_context, staff_authorization_scope
from app.services.appointment_common import ensure_admin, ensure_owner_admin


def context(
    role: StaffRole,
    *,
    telegram_id: int = 101,
    business_id: int = 1,
) -> StaffContext:
    return StaffContext(
        business_id=business_id,
        staff_member_id=2,
        user_id=3,
        telegram_id=telegram_id,
        display_name="Staff",
        role=role,
        is_bookable=role is StaffRole.MASTER,
    )


def test_legacy_numeric_ids_remain_available_only_without_runtime_context() -> None:
    ensure_admin(AdminActor(telegram_id=101), frozenset({101}))

    with pytest.raises(AuthorizationError):
        ensure_admin(AdminActor(telegram_id=202), frozenset({101}))


def test_server_derived_staff_context_is_immutable() -> None:
    target = context(StaffRole.MASTER)

    with pytest.raises(ValidationError, match="frozen"):
        target.role = StaffRole.OWNER  # type: ignore[misc]


@pytest.mark.parametrize(
    "role",
    [StaffRole.OWNER, StaffRole.MANAGER, StaffRole.RECEPTIONIST],
)
def test_fresh_db_context_authorizes_legacy_admin_roles_without_env_ids(role: StaffRole) -> None:
    with staff_authorization_scope(context(role)):
        ensure_admin(AdminActor(telegram_id=101), frozenset())


def test_master_context_cannot_reach_legacy_bare_id_crud() -> None:
    with staff_authorization_scope(context(StaffRole.MASTER)):
        with pytest.raises(AuthorizationError):
            ensure_admin(AdminActor(telegram_id=101), frozenset({101}))


def test_runtime_context_must_match_actor_even_if_actor_is_in_env() -> None:
    with staff_authorization_scope(context(StaffRole.OWNER, telegram_id=101)):
        with pytest.raises(AuthorizationError):
            ensure_admin(AdminActor(telegram_id=202), frozenset({202}))


def test_permanent_delete_requires_owner_in_same_business() -> None:
    actor = AdminActor(telegram_id=101)
    with staff_authorization_scope(context(StaffRole.MANAGER)):
        with pytest.raises(AuthorizationError, match="только владельцу"):
            ensure_owner_admin(actor, frozenset(), business_id=1)

    with staff_authorization_scope(context(StaffRole.OWNER, business_id=2)):
        with pytest.raises(AuthorizationError, match="другого бизнеса"):
            ensure_owner_admin(actor, frozenset(), business_id=1)

    with staff_authorization_scope(context(StaffRole.OWNER, business_id=1)):
        ensure_owner_admin(actor, frozenset(), business_id=1)


@pytest.mark.asyncio
async def test_middleware_binds_and_resets_verified_context() -> None:
    target = context(StaffRole.OWNER)

    async def handler(event: TelegramObject, data: dict[str, Any]) -> str:
        del event, data
        assert get_staff_context() == target
        ensure_admin(AdminActor(telegram_id=101), frozenset())
        return "ok"

    result = await StaffContextMiddleware()(
        handler,
        TelegramObject(),
        {"staff_context": target},
    )

    assert result == "ok"
    assert get_staff_context() is None


@pytest.mark.asyncio
async def test_middleware_fails_closed_without_filter_context() -> None:
    async def handler(event: TelegramObject, data: dict[str, Any]) -> None:
        del event, data

    with pytest.raises(AuthorizationError):
        await StaffContextMiddleware()(handler, TelegramObject(), {})


@pytest.mark.asyncio
async def test_runtime_middleware_disables_env_fallback_without_db_context() -> None:
    async def handler(event: TelegramObject, data: dict[str, Any]) -> None:
        del event, data
        ensure_admin(AdminActor(telegram_id=101), frozenset({101}))

    with pytest.raises(AuthorizationError):
        await RuntimeAuthorizationMiddleware()(handler, TelegramObject(), {})

    # ContextVar state must not leak into offline compatibility calls.
    ensure_admin(AdminActor(telegram_id=101), frozenset({101}))
