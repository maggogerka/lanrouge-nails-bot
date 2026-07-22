"""Administrative reply menu."""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

ADMIN_SERVICES_TEXT = "💅 Услуги"


def admin_main_keyboard() -> ReplyKeyboardMarkup:
    """Return only implemented administrative sections."""

    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=ADMIN_SERVICES_TEXT)]],
        resize_keyboard=True,
        input_field_placeholder="Меню администратора",
    )
