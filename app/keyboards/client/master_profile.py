"""Safe URL buttons for the published master profile."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.schemas.master_profile import MasterProfileView


def master_profile_links_keyboard(profile: MasterProfileView) -> InlineKeyboardMarkup | None:
    rows: list[list[InlineKeyboardButton]] = []
    if profile.map_url:
        rows.append([InlineKeyboardButton(text="🗺 Открыть карту", url=profile.map_url)])
    if profile.telegram_url:
        rows.append([InlineKeyboardButton(text="💬 Написать мастеру", url=profile.telegram_url)])
    rows.extend([[InlineKeyboardButton(text=link.label, url=link.url)] for link in profile.links])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
