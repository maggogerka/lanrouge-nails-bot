"""Last-resort Telegram error boundary."""

from __future__ import annotations

import logging

from aiogram.types import ErrorEvent, Message

from app.logging import log_event

logger = logging.getLogger(__name__)

_FRIENDLY_ERROR = "Что-то пошло не так. Попробуйте ещё раз немного позже."


async def handle_unexpected_error(event: ErrorEvent) -> bool:
    """Hide tracebacks from users while retaining technical diagnostics."""

    exception = event.exception
    logger.error(
        "telegram_update_failed",
        extra={"event": "telegram.update_failed"},
        exc_info=(type(exception), exception, exception.__traceback__),
    )

    message = event.update.message
    if message is None and event.update.callback_query is not None:
        callback_message = event.update.callback_query.message
        if isinstance(callback_message, Message):
            message = callback_message

    if message is not None:
        try:
            await message.answer(_FRIENDLY_ERROR)
        except Exception:
            log_event(logger, logging.ERROR, "telegram.error_reply_failed")
    return True
