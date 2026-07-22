"""Repeat-booking reminder and offer actions."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.keyboards.client.portfolio import PortfolioClientCallback


class RepeatBookingCallback(CallbackData, prefix="repeat"):
    action: str


def repeat_reminder_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Записаться снова",
                    callback_data=RepeatBookingCallback(action="start").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Посмотреть работы",
                    callback_data=PortfolioClientCallback(action="page", page=1).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Больше не напоминать",
                    callback_data=RepeatBookingCallback(action="opt_out").pack(),
                )
            ],
        ]
    )
