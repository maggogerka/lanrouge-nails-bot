"""Fail-closed anti-spam and callback replay protection for the public demo."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from hashlib import sha256
from typing import Any, Protocol

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

from app.security.rate_limit import RedisEvalClient, RedisRateLimiter

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class DemoRedis(RedisEvalClient, Protocol):
    async def set(
        self, name: str, value: str, *, ex: int | None = None, nx: bool = False
    ) -> object: ...


class DemoGuardMiddleware(BaseMiddleware):
    """Enforce the global 30/minute limit and one-time callback processing."""

    def __init__(self, redis: DemoRedis, *, namespace: str, limit: int) -> None:
        self._redis = redis
        self._limiter = RedisRateLimiter(redis, namespace=namespace)
        self._namespace = namespace
        self._limit = limit

    async def __call__(
        self, handler: Handler, event: TelegramObject, data: dict[str, Any]
    ) -> Any:
        if not isinstance(event, (Message, CallbackQuery)) or event.from_user is None:
            return await handler(event, data)
        raw_request_id = (
            event.id
            if isinstance(event, CallbackQuery)
            else f"{event.chat.id}:{event.message_id}"
        )
        request_id = f"demo-{sha256(raw_request_id.encode('utf-8')).hexdigest()}"
        try:
            decision = await self._limiter.consume(
                "demo_actions",
                business_id=1,
                subject_id=event.from_user.id,
                request_id=request_id,
                limit=self._limit,
                window_seconds=60,
            )
            if not decision.allowed:
                await self._reject(
                    event,
                    f"Слишком много действий. Повторите через {decision.retry_after_seconds} сек.",
                )
                return None
            if isinstance(event, CallbackQuery):
                callback_key = f"{self._namespace}:demo_callback:{request_id}"
                first = await self._redis.set(callback_key, "1", ex=300, nx=True)
                if not first:
                    await event.answer("Эта кнопка уже обработана.")
                    return None
        except Exception:
            await self._reject(event, "Защита демо временно недоступна. Попробуйте позже.")
            return None
        return await handler(event, data)

    @staticmethod
    async def _reject(event: Message | CallbackQuery, text: str) -> None:
        if isinstance(event, CallbackQuery):
            await event.answer(text, show_alert=True)
        else:
            await event.answer(text)
