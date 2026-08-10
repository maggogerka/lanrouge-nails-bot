"""White-label support and legal-link keyboards."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.schemas.presentation import BusinessPresentation


def business_links_keyboard(
    business: BusinessPresentation,
) -> InlineKeyboardMarkup | None:
    rows: list[list[InlineKeyboardButton]] = []
    if business.map_url is not None:
        rows.append([InlineKeyboardButton(text="📍 Открыть на карте", url=business.map_url)])
    if business.support_url is not None:
        rows.append([InlineKeyboardButton(text="💬 Написать", url=business.support_url)])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None


def privacy_links_keyboard(
    business: BusinessPresentation,
) -> InlineKeyboardMarkup | None:
    rows: list[list[InlineKeyboardButton]] = []
    if business.privacy_policy_url is not None:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Политика конфиденциальности",
                    url=business.privacy_policy_url,
                )
            ]
        )
    if business.terms_url is not None:
        rows.append([InlineKeyboardButton(text="Условия использования", url=business.terms_url)])
    return InlineKeyboardMarkup(inline_keyboard=rows) if rows else None
