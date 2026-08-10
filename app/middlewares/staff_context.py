"""Bind DB-derived staff authorization to protected handler execution."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject

from app.domain.errors import AuthorizationError
from app.schemas.authorization import StaffContext
from app.security import db_staff_authorization_required_scope, staff_authorization_scope

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class StaffContextMiddleware(BaseMiddleware):
    """Fail closed unless the parent DB filter injected a verified context."""

    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        context = data.get("staff_context")
        if not isinstance(context, StaffContext):
            raise AuthorizationError("Administrative access denied")
        with staff_authorization_scope(context):
            return await handler(event, data)


class RuntimeAuthorizationMiddleware(BaseMiddleware):
    """Disable environment-ID fallback for every production Telegram update."""

    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        with db_staff_authorization_required_scope():
            return await handler(event, data)
