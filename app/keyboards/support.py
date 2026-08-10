"""Shared vendor-support link keyboard for authorized staff."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def vendor_support_keyboard(url: str | None) -> InlineKeyboardMarkup | None:
    if url is None:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Открыть техническую поддержку", url=url)]]
    )
