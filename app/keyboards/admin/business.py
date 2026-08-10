"""White-label business profile controls."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class BusinessProfileCallback(CallbackData, prefix="biz"):
    action: str


def business_profile_keyboard() -> InlineKeyboardMarkup:
    actions = (
        (("Название", "name"), ("Описание", "description")),
        (("Короткое описание", "short"), ("Тип solo/salon", "type")),
        (("Телефон", "phone"), ("Адрес", "address")),
        (("Часовой пояс", "timezone"), ("Логотип", "logo")),
        (("Политика", "privacy"), ("Оферта", "terms")),
        (("Поддержка клиента", "support_name"), ("Ссылка поддержки", "support_url")),
        (("💼 CRM-подписка", "subscription"),),
        (("🔄 Синхронизировать профиль бота", "sync_bot"),),
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=BusinessProfileCallback(action=action).pack(),
                )
                for label, action in row
            ]
            for row in actions
        ]
    )
