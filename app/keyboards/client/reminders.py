"""Client action embedded into the 24-hour reminder."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class ReminderCallback(CallbackData, prefix="rem"):
    action: str
    appointment_id: int


def confirm_visit_keyboard(appointment_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Подтверждаю визит",
                    callback_data=ReminderCallback(
                        action="confirm",
                        appointment_id=appointment_id,
                    ).pack(),
                )
            ]
        ]
    )
