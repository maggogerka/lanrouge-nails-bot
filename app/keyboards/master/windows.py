"""Self-scoped master availability controls."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class MasterWindowFormCallback(CallbackData, prefix="mwf"):
    action: str


def master_window_duration_keyboard(default_minutes: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"✅ По умолчанию · {default_minutes} мин.",
                    callback_data=MasterWindowFormCallback(action="duration_default").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Другая длительность",
                    callback_data=MasterWindowFormCallback(action="duration_manual").pack(),
                )
            ],
            [_cancel_button()],
        ]
    )


def master_window_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Открыть окно",
                    callback_data=MasterWindowFormCallback(action="create").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="📅 Изменить дату",
                    callback_data=MasterWindowFormCallback(action="edit_date").pack(),
                ),
                InlineKeyboardButton(
                    text="🕒 Изменить время",
                    callback_data=MasterWindowFormCallback(action="edit_time").pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⏱ Изменить длительность",
                    callback_data=MasterWindowFormCallback(action="edit_duration").pack(),
                )
            ],
            [_cancel_button()],
        ]
    )


def master_window_created_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Открыть ещё одно окно",
                    callback_data=MasterWindowFormCallback(action="another").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Готово",
                    callback_data=MasterWindowFormCallback(action="done").pack(),
                )
            ],
        ]
    )


def _cancel_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text="❌ Отмена",
        callback_data=MasterWindowFormCallback(action="cancel").pack(),
    )
