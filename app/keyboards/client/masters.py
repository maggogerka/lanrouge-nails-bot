"""Public master-card actions."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class PublicMasterCallback(CallbackData, prefix="pmaster"):
    action: str
    staff_member_id: int


def public_master_keyboard(staff_member_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✨ Записаться к мастеру",
                    callback_data=PublicMasterCallback(
                        action="book",
                        staff_member_id=staff_member_id,
                    ).pack(),
                )
            ]
        ]
    )
