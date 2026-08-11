"""Client portfolio navigation, filters, booking and sharing controls."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.schemas.portfolio import PortfolioItemView, PortfolioTagView


class PortfolioClientCallback(CallbackData, prefix="cpf"):
    action: str
    object_id: int = 0
    page: int = 1
    tag_id: int = 0


def portfolio_card_keyboard(
    item: PortfolioItemView,
    *,
    page: int,
    pages: int,
    tag_id: int = 0,
    share_url: str | None = None,
) -> InlineKeyboardMarkup:
    navigation = []
    if page > 1:
        navigation.append(_callback_button("◀️ Предыдущая", "page", page - 1, tag_id=tag_id))
    if page < pages:
        navigation.append(_callback_button("Следующая ▶️", "page", page + 1, tag_id=tag_id))
    rows: list[list[InlineKeyboardButton]] = []
    if navigation:
        rows.append(navigation)
    rows.append([_callback_button("🏷 Теги", "tags", page, object_id=item.id)])
    rows.append([_callback_button("Хочу похожий результат", "similar", page, object_id=item.id)])
    rows.append([_callback_button("📅 Записаться", "book", page, object_id=item.id)])
    if share_url is not None:
        rows.append([InlineKeyboardButton(text="Поделиться работой", url=share_url)])
    rows.append([_callback_button("🔙 Главное меню", "close", page)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def portfolio_tags_keyboard(tags: list[PortfolioTagView]) -> InlineKeyboardMarkup:
    rows = [[_callback_button(f"🏷 {tag.name}", "page", 1, tag_id=tag.id)] for tag in tags]
    rows.append([_callback_button("Все работы", "page", 1)])
    rows.append([_callback_button("🔙 Главное меню", "close", 1)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def external_portfolio_keyboard(url: str, button_text: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=button_text, url=url)]])


def _callback_button(
    text: str,
    action: str,
    page: int,
    *,
    object_id: int = 0,
    tag_id: int = 0,
) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        callback_data=PortfolioClientCallback(
            action=action,
            object_id=object_id,
            page=page,
            tag_id=tag_id,
        ).pack(),
    )
