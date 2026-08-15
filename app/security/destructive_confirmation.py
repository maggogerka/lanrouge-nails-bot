"""Atomic, actor-bound confirmations for irreversible administrative actions."""

from __future__ import annotations

import secrets
from enum import StrEnum
from typing import Protocol

from app.domain.enums import StaffRole
from app.domain.errors import AuthorizationError
from app.schemas.authorization import StaffContext


class DestructiveObjectType(StrEnum):
    SERVICE = "service"
    WINDOW = "window"
    REVIEW = "review"


class ConfirmationRedis(Protocol):
    async def set(
        self,
        name: str,
        value: str,
        *,
        ex: int,
    ) -> object: ...

    async def getdel(self, name: str) -> bytes | str | None: ...


class DestructiveConfirmationService:
    """Issue short-lived confirmations and atomically consume each exactly once."""

    def __init__(
        self,
        redis: ConfirmationRedis,
        *,
        namespace: str,
        ttl_seconds: int = 300,
    ) -> None:
        if not 30 <= ttl_seconds <= 900:
            raise ValueError("destructive confirmation TTL must be between 30 and 900 seconds")
        self._redis = redis
        self._namespace = namespace
        self._ttl_seconds = ttl_seconds

    async def issue(
        self,
        actor: StaffContext,
        object_type: DestructiveObjectType,
        object_id: int,
    ) -> None:
        self._require_owner(actor)
        await self._redis.set(
            self._key(actor, object_type, object_id),
            secrets.token_urlsafe(24),
            ex=self._ttl_seconds,
        )

    async def consume(
        self,
        actor: StaffContext,
        object_type: DestructiveObjectType,
        object_id: int,
    ) -> None:
        self._require_owner(actor)
        value = await self._redis.getdel(self._key(actor, object_type, object_id))
        if value is None:
            raise AuthorizationError(
                "Подтверждение отсутствует, уже использовано или истекло. Начните удаление заново."
            )

    def _key(
        self,
        actor: StaffContext,
        object_type: DestructiveObjectType,
        object_id: int,
    ) -> str:
        if object_id <= 0:
            raise AuthorizationError("Некорректный объект удаления.")
        return (
            f"{self._namespace}:destructive-confirmation:"
            f"{actor.business_id}:{actor.user_id}:{object_type.value}:{object_id}"
        )

    @staticmethod
    def _require_owner(actor: StaffContext) -> None:
        if actor.role is not StaffRole.OWNER and not actor.is_bootstrap_owner:
            raise AuthorizationError("Только владелец может подтверждать необратимое удаление.")
