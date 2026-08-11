"""Clear incompatible FSM drafts before dispatching top-level navigation."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, TelegramObject

from app.keyboards.admin import main as admin_main
from app.keyboards.admin.services import CANCEL_TEXT
from app.keyboards.client import main as client_main
from app.keyboards.master import main as master_main

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]

_GLOBAL_COMMANDS = frozenset({"/start", "/admin", "/master"})


def _menu_texts(module: object) -> frozenset[str]:
    return frozenset(
        value
        for name, value in vars(module).items()
        if name.endswith("_TEXT") and isinstance(value, str)
    )


GLOBAL_NAVIGATION_TEXTS = frozenset({CANCEL_TEXT}).union(
    _menu_texts(admin_main),
    _menu_texts(client_main),
    _menu_texts(master_main),
)


class GlobalNavigationMiddleware(BaseMiddleware):
    """Drop an old draft before filters evaluate a new top-level destination."""

    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message) and self.is_navigation(event.text):
            state = data.get("state")
            if isinstance(state, FSMContext):
                await state.clear()
        return await handler(event, data)

    @staticmethod
    def is_navigation(text: str | None) -> bool:
        normalized = (text or "").strip()
        if normalized in GLOBAL_NAVIGATION_TEXTS:
            return True
        command = normalized.split(maxsplit=1)[0].casefold().split("@", maxsplit=1)[0]
        return command in _GLOBAL_COMMANDS
