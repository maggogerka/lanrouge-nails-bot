"""Inline controls for portfolio administration and creation."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.domain.enums import PortfolioDisplayMode, PortfolioStatus
from app.schemas.portfolio import PortfolioDisplayConfig, PortfolioItemView
from app.schemas.service import ServiceView


class PortfolioAdminCallback(CallbackData, prefix="apf"):
    action: str
    object_id: int = 0
    page: int = 1


def portfolio_admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("➕ Добавить работу", "add")],
            [_button("📋 Все работы", "list")],
            [_button("🏷 Категории и теги", "tags")],
            [_button("👁 Опубликованные", "published")],
            [_button("🗄 Архив", "archived")],
            [_button("⚙️ Режим показа", "display")],
        ]
    )


def portfolio_list_keyboard(
    items: list[PortfolioItemView],
    *,
    page: int,
    pages: int,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{_status_icon(item.status)} {item.title}",
                callback_data=PortfolioAdminCallback(
                    action="view", object_id=item.id, page=page
                ).pack(),
            )
        ]
        for item in items
    ]
    navigation = []
    if page > 1:
        navigation.append(
            InlineKeyboardButton(
                text="◀️",
                callback_data=PortfolioAdminCallback(action="list", page=page - 1).pack(),
            )
        )
    if page < pages:
        navigation.append(
            InlineKeyboardButton(
                text="▶️",
                callback_data=PortfolioAdminCallback(action="list", page=page + 1).pack(),
            )
        )
    if navigation:
        rows.append(navigation)
    rows.append([_button("🔙 Портфолио", "menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def portfolio_details_keyboard(item: PortfolioItemView, *, page: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if item.status is not PortfolioStatus.PUBLISHED:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📢 Опубликовать",
                    callback_data=PortfolioAdminCallback(
                        action="publish", object_id=item.id, page=page
                    ).pack(),
                )
            ]
        )
    if item.status is not PortfolioStatus.ARCHIVED:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🗄 Архивировать",
                    callback_data=PortfolioAdminCallback(
                        action="archive", object_id=item.id, page=page
                    ).pack(),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="🔙 К списку",
                callback_data=PortfolioAdminCallback(action="list", page=page).pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def media_collection_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("✅ Фото загружены", "media_done")],
            [_button("❌ Отменить", "cancel")],
        ]
    )


def linked_service_keyboard(services: list[ServiceView]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=service.name,
                callback_data=PortfolioAdminCallback(action="link", object_id=service.id).pack(),
            )
        ]
        for service in services
    ]
    rows.append([_button("Без связи с услугой", "link_none")])
    rows.append([_button("❌ Отменить", "cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def portfolio_preview_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("📢 Опубликовать", "save_publish")],
            [_button("💾 Сохранить черновик", "save_draft")],
            [_button("❌ Отменить", "cancel")],
        ]
    )


def portfolio_display_keyboard(config: PortfolioDisplayConfig) -> InlineKeyboardMarkup:
    def marker(mode: PortfolioDisplayMode) -> str:
        return "✅" if config.mode is mode else "▫️"

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button(
                    f"{marker(PortfolioDisplayMode.INTERNAL)} Встроенное",
                    "mode_internal",
                )
            ],
            [
                _button(
                    f"{marker(PortfolioDisplayMode.EXTERNAL_LINK)} Внешняя ссылка",
                    "mode_external",
                )
            ],
            [
                _button(
                    f"{marker(PortfolioDisplayMode.DISABLED)} Выключено",
                    "mode_disabled",
                )
            ],
            [_button("✏️ Изменить внешний URL", "edit_external_url")],
            [_button("✏️ Текст внешней кнопки", "edit_external_text")],
            [_button("👁 Предпросмотр", "display_preview")],
            [_button("🔙 Портфолио", "menu")],
        ]
    )


def _button(text: str, action: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        callback_data=PortfolioAdminCallback(action=action).pack(),
    )


def _status_icon(status: PortfolioStatus) -> str:
    return {
        PortfolioStatus.DRAFT: "📝",
        PortfolioStatus.PUBLISHED: "✅",
        PortfolioStatus.ARCHIVED: "🗄",
    }[status]
