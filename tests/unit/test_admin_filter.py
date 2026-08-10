"""DB-backed staff filter tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Message

from app.domain.enums import StaffRole
from app.domain.errors import AuthorizationError
from app.filters.admin import IsStaff
from app.schemas.authorization import StaffContext
from app.services.authorization_service import AuthorizationService


def staff_context(role: StaffRole = StaffRole.OWNER, *, telegram_id: int = 101) -> StaffContext:
    return StaffContext(
        business_id=1,
        staff_member_id=2,
        user_id=3,
        telegram_id=telegram_id,
        display_name="Staff",
        role=role,
        is_bookable=role is StaffRole.MASTER,
    )


def authorization_service(result: StaffContext | Exception) -> AuthorizationService:
    service = MagicMock(spec=AuthorizationService)
    service.authorize = AsyncMock(
        side_effect=result if isinstance(result, Exception) else None,
        return_value=None if isinstance(result, Exception) else result,
    )
    return cast(AuthorizationService, service)


@pytest.mark.asyncio
async def test_staff_filter_resolves_membership_and_injects_context_on_every_update() -> None:
    event = cast(Message, SimpleNamespace(from_user=SimpleNamespace(id=101)))
    context = staff_context()
    service = authorization_service(context)
    target = IsStaff(allowed_roles={StaffRole.OWNER, StaffRole.MANAGER})

    first = await target(event, service)
    second = await target(event, service)

    assert first == {"staff_context": context}
    assert second == {"staff_context": context}
    assert cast(AsyncMock, service.authorize).await_count == 2
    cast(AsyncMock, service.authorize).assert_awaited_with(business_id=1, telegram_id=101)


@pytest.mark.asyncio
async def test_legacy_admin_filter_rejects_master_role() -> None:
    event = cast(Message, SimpleNamespace(from_user=SimpleNamespace(id=101)))
    service = authorization_service(staff_context(StaffRole.MASTER))

    result = await IsStaff(
        allowed_roles={StaffRole.OWNER, StaffRole.MANAGER, StaffRole.RECEPTIONIST}
    )(event, service)

    assert result is False


@pytest.mark.asyncio
async def test_staff_filter_rejects_revoked_membership_without_env_fallback() -> None:
    event = cast(Message, SimpleNamespace(from_user=SimpleNamespace(id=101)))
    service = authorization_service(AuthorizationError("revoked"))

    assert not await IsStaff(allowed_roles={StaffRole.OWNER})(event, service)


@pytest.mark.asyncio
async def test_staff_filter_rejects_missing_sender_without_db_query() -> None:
    event = cast(Message, SimpleNamespace(from_user=None))
    service = authorization_service(staff_context())

    assert not await IsStaff(allowed_roles={StaffRole.OWNER})(event, service)
    cast(AsyncMock, service.authorize).assert_not_awaited()
