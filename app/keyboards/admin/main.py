"""Administrative reply menu."""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

ADMIN_SERVICES_TEXT = "💅 Услуги"
ADMIN_ADD_WINDOW_TEXT = "➕ Добавить окно"
ADMIN_WINDOWS_TEXT = "🕒 Открытые окна"
ADMIN_TODAY_TEXT = "📅 Сегодня"
ADMIN_UPCOMING_TEXT = "🗓 Ближайшие записи"
ADMIN_CLIENTS_TEXT = "👥 Клиентки"
ADMIN_PORTFOLIO_TEXT = "🖼 Портфолио"
ADMIN_SETTINGS_TEXT = "⚙️ Настройки"
ADMIN_WAITLIST_TEXT = "⏳ Лист ожидания"
ADMIN_REVIEWS_TEXT = "⭐ Отзывы"
ADMIN_BROADCASTS_TEXT = "📢 Рассылки"


ADMIN_MASTER_PROFILE_TEXT = "ℹ️ Информация о мастере"


def admin_main_keyboard() -> ReplyKeyboardMarkup:
    """Return only implemented administrative sections."""

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=ADMIN_TODAY_TEXT),
                KeyboardButton(text=ADMIN_UPCOMING_TEXT),
            ],
            [
                KeyboardButton(text=ADMIN_ADD_WINDOW_TEXT),
                KeyboardButton(text=ADMIN_WINDOWS_TEXT),
            ],
            [
                KeyboardButton(text=ADMIN_SERVICES_TEXT),
                KeyboardButton(text=ADMIN_CLIENTS_TEXT),
            ],
            [
                KeyboardButton(text=ADMIN_PORTFOLIO_TEXT),
                KeyboardButton(text=ADMIN_WAITLIST_TEXT),
            ],
            [KeyboardButton(text=ADMIN_REVIEWS_TEXT)],
            [KeyboardButton(text=ADMIN_BROADCASTS_TEXT)],
            [KeyboardButton(text=ADMIN_MASTER_PROFILE_TEXT)],
            [KeyboardButton(text=ADMIN_SETTINGS_TEXT)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Меню администратора",
    )
