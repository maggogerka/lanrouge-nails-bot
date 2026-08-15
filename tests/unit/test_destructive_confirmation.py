"""Atomic irreversible-action confirmation regressions."""

from __future__ import annotations

import pytest

from app.domain.enums import StaffRole
from app.domain.errors import AuthorizationError
from app.schemas.authorization import StaffContext
from app.security.destructive_confirmation import (
    DestructiveConfirmationService,
    DestructiveObjectType,
)


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: dict[str, int] = {}

    async def set(self, name: str, value: str, *, ex: int) -> object:
        self.values[name] = value
        self.expirations[name] = ex
        return True

    async def getdel(self, name: str) -> str | None:
        self.expirations.pop(name, None)
        return self.values.pop(name, None)


def actor(
    *,
    user_id: int = 10,
    business_id: int = 1,
    role: StaffRole = StaffRole.OWNER,
) -> StaffContext:
    return StaffContext(
        business_id=business_id,
        staff_member_id=user_id,
        user_id=user_id,
        telegram_id=1000 + user_id,
        display_name="Owner",
        role=role,
        is_bookable=False,
    )


@pytest.mark.asyncio
async def test_confirmation_is_actor_business_target_bound_and_single_use() -> None:
    redis = FakeRedis()
    service = DestructiveConfirmationService(redis, namespace="test", ttl_seconds=60)
    owner = actor()

    await service.issue(owner, DestructiveObjectType.SERVICE, 7)

    key = "test:destructive-confirmation:1:10:service:7"
    assert redis.expirations[key] == 60
    await service.consume(owner, DestructiveObjectType.SERVICE, 7)
    with pytest.raises(AuthorizationError, match="уже использовано"):
        await service.consume(owner, DestructiveObjectType.SERVICE, 7)


@pytest.mark.asyncio
async def test_foreign_wrong_target_and_expired_confirmations_are_rejected() -> None:
    redis = FakeRedis()
    service = DestructiveConfirmationService(redis, namespace="test", ttl_seconds=60)
    owner = actor()
    await service.issue(owner, DestructiveObjectType.WINDOW, 8)

    for wrong_actor, object_type, object_id in (
        (actor(user_id=11), DestructiveObjectType.WINDOW, 8),
        (actor(business_id=2), DestructiveObjectType.WINDOW, 8),
        (owner, DestructiveObjectType.REVIEW, 8),
        (owner, DestructiveObjectType.WINDOW, 9),
    ):
        with pytest.raises(AuthorizationError, match="отсутствует"):
            await service.consume(wrong_actor, object_type, object_id)

    redis.values.clear()  # simulate Redis TTL expiry
    with pytest.raises(AuthorizationError, match="истекло"):
        await service.consume(owner, DestructiveObjectType.WINDOW, 8)


@pytest.mark.asyncio
async def test_non_owner_cannot_issue_or_consume_confirmation() -> None:
    redis = FakeRedis()
    service = DestructiveConfirmationService(redis, namespace="test")
    manager = actor(role=StaffRole.MANAGER)

    with pytest.raises(AuthorizationError, match="Только владелец"):
        await service.issue(manager, DestructiveObjectType.REVIEW, 5)
    with pytest.raises(AuthorizationError, match="Только владелец"):
        await service.consume(manager, DestructiveObjectType.REVIEW, 5)
    assert redis.values == {}
