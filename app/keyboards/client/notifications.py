"""Independent client notification preference controls."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.schemas.booking import NotificationPreferences


class NotificationSettingsCallback(CallbackData, prefix="notify_settings"):
    action: str


def notification_settings_keyboard(
    preferences: NotificationPreferences,
) -> InlineKeyboardMarkup:
    marketing_action = "marketing_off" if preferences.marketing_enabled else "marketing_on"
    repeat_action = "repeat_off" if preferences.repeat_booking_enabled else "repeat_on"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=(
                        "Отключить рекламные сообщения"
                        if preferences.marketing_enabled
                        else "Включить рекламные сообщения"
                    ),
                    callback_data=NotificationSettingsCallback(action=marketing_action).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text=(
                        "Отключить напоминания о повторе"
                        if preferences.repeat_booking_enabled
                        else "Включить напоминания о повторе"
                    ),
                    callback_data=NotificationSettingsCallback(action=repeat_action).pack(),
                )
            ],
        ]
    )
