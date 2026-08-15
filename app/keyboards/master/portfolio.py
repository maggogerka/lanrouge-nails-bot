"""Self-scoped master portfolio controls."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.domain.enums import PortfolioStatus
from app.schemas.portfolio import PortfolioItemView


class MasterPortfolioCallback(CallbackData, prefix="mpf"):
    action: str
    item_id: int = 0
    page: int = 1


def master_portfolio_menu(
    items: list[PortfolioItemView], *, page: int = 1, pages: int = 1
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{_icon(item.status)} {item.title[:40]}",
                callback_data=MasterPortfolioCallback(
                    action="view",
                    item_id=item.id,
                    page=page,
                ).pack(),
            )
        ]
        for item in items
    ]
    if pages > 1:
        navigation: list[InlineKeyboardButton] = []
        if page > 1:
            navigation.append(
                InlineKeyboardButton(
                    text="◀️",
                    callback_data=MasterPortfolioCallback(action="list", page=page - 1).pack(),
                )
            )
        navigation.append(
            InlineKeyboardButton(
                text=f"{page}/{pages}",
                callback_data=MasterPortfolioCallback(action="list", page=page).pack(),
            )
        )
        if page < pages:
            navigation.append(
                InlineKeyboardButton(
                    text="▶️",
                    callback_data=MasterPortfolioCallback(action="list", page=page + 1).pack(),
                )
            )
        rows.append(navigation)
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


def master_portfolio_item_keyboard(
    item: PortfolioItemView, *, page: int = 1
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if item.status is not PortfolioStatus.PUBLISHED:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📢 Опубликовать",
                    callback_data=MasterPortfolioCallback(
                        action="publish",
                        item_id=item.id,
                        page=page,
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
                        page=page,
                    ).pack(),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Моё портфолио",
                callback_data=MasterPortfolioCallback(action="list", page=page).pack(),
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
