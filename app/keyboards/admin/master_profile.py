"""Inline controls for the public master profile."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.schemas.master_profile import MasterProfileView


class MasterProfileAdminCallback(CallbackData, prefix="amp"):
    action: str
    object_id: int = 0


def master_profile_keyboard(profile: MasterProfileView) -> InlineKeyboardMarkup:
    publish_text = "📴 Снять с публикации" if profile.is_published else "✅ Опубликовать"
    publish_action = "unpublish" if profile.is_published else "publish"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("✏️ Имя", "edit_name"), _button("📝 Описание", "edit_bio")],
            [_button("📷 Фото", "edit_photo"), _button("🗑 Убрать фото", "remove_photo")],
            [_button("📍 Адрес", "edit_address"), _button("🗺 Ссылка на карту", "edit_map")],
            [_button("💬 Telegram", "edit_telegram")],
            [_button("🔗 Публичные ссылки", "links")],
            [_button("👁 Предпросмотр", "preview")],
            [_button(publish_text, publish_action)],
        ]
    )


def master_links_keyboard(profile: MasterProfileView) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'✅' if link.is_active else '▫️'} {link.sort_order}: {link.label}",
                callback_data=MasterProfileAdminCallback(
                    action="edit_link", object_id=link.id
                ).pack(),
            ),
            InlineKeyboardButton(
                text="🗑",
                callback_data=MasterProfileAdminCallback(
                    action="delete_link", object_id=link.id
                ).pack(),
            ),
        ]
        for link in profile.links
    ]
    rows.extend(
        [
            [_button("➕ Добавить ссылку", "add_link")],
            [_button("🔙 К профилю", "menu")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _button(text: str, action: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        callback_data=MasterProfileAdminCallback(action=action).pack(),
    )
