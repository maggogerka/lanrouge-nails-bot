"""Self-scoped master portfolio controls."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.domain.enums import PortfolioStatus
from app.schemas.portfolio import PortfolioItemView


class MasterPortfolioCallback(CallbackData, prefix="mpf"):
    action: str
    item_id: int = 0


def master_portfolio_menu(items: list[PortfolioItemView]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{_icon(item.status)} {item.title[:40]}",
                callback_data=MasterPortfolioCallback(
                    action="view",
                    item_id=item.id,
                ).pack(),
            )
        ]
        for item in items
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="➕ Добавить работу",
                callback_data=MasterPortfolioCallback(action="add").pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def master_portfolio_media_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Фото загружены",
                    callback_data=MasterPortfolioCallback(action="media_done").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data=MasterPortfolioCallback(action="cancel").pack(),
                )
            ],
        ]
    )


def master_portfolio_save_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📢 Опубликовать",
                    callback_data=MasterPortfolioCallback(action="save_publish").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="💾 Сохранить черновик",
                    callback_data=MasterPortfolioCallback(action="save_draft").pack(),
                )
            ],
        ]
    )


def master_portfolio_item_keyboard(item: PortfolioItemView) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if item.status is not PortfolioStatus.PUBLISHED:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📢 Опубликовать",
                    callback_data=MasterPortfolioCallback(
                        action="publish",
                        item_id=item.id,
                    ).pack(),
                )
            ]
        )
    if item.status is not PortfolioStatus.ARCHIVED:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📦 Архивировать",
                    callback_data=MasterPortfolioCallback(
                        action="archive",
                        item_id=item.id,
                    ).pack(),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Моё портфолио",
                callback_data=MasterPortfolioCallback(action="list").pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _icon(status: PortfolioStatus) -> str:
    return {
        PortfolioStatus.DRAFT: "📝",
        PortfolioStatus.PUBLISHED: "✅",
        PortfolioStatus.ARCHIVED: "📦",
    }[status]
