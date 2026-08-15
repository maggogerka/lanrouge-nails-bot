"""Small lifecycle adapters used by the executable API composition root."""

from __future__ import annotations

from collections.abc import Awaitable, Callable


class CloseOnlyResource:
    """Expose an async closer through the common ASGI lifecycle contract."""

    def __init__(self, close: Callable[[], Awaitable[None]]) -> None:
        self._close = close

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        await self._close()
