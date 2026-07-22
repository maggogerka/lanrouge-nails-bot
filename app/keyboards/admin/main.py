"""Administrative reply menu."""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

ADMIN_SERVICES_TEXT = "💅 Услуги"
ADMIN_ADD_WINDOW_TEXT = "➕ Добавить окно"
ADMIN_WINDOWS_TEXT = "🕒 Открытые окна"


def admin_main_keyboard() -> ReplyKeyboardMarkup:
    """Return only implemented administrative sections."""

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=ADMIN_ADD_WINDOW_TEXT),
                KeyboardButton(text=ADMIN_WINDOWS_TEXT),
            ],
            [KeyboardButton(text=ADMIN_SERVICES_TEXT)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Меню администратора",
    )
