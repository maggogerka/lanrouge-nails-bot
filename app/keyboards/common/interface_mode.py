"""Server-authorized switch between staff and client presentation modes."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


class InterfaceModeCallback(CallbackData, prefix="mode"):
    action: str


def interface_mode_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Посмотреть бот глазами клиента",
                    callback_data=InterfaceModeCallback(action="client").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Открыть панель управления",
                    callback_data=InterfaceModeCallback(action="management").pack(),
                )
            ],
        ]
    )


def return_to_management_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Вернуться в панель управления",
                    callback_data=InterfaceModeCallback(action="management").pack(),
                )
            ]
        ]
    )
