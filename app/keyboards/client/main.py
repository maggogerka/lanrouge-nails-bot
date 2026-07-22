"""Client main menu containing only usable or gracefully deferred sections."""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

CLIENT_BOOK_TEXT = "💅 Записаться"
CLIENT_APPOINTMENTS_TEXT = "📅 Моя запись"
CLIENT_SERVICES_TEXT = "💵 Услуги и цены"
CLIENT_CONTACTS_TEXT = "📍 Адрес и контакты"
CLIENT_PORTFOLIO_TEXT = "🖼 Работы мастера"
CLIENT_PREPARATION_TEXT = "❓ Подготовка к процедуре"
CLIENT_NOTIFICATIONS_TEXT = "🔔 Настройки уведомлений"
CLIENT_WAITLIST_TEXT = "⏳ Лист ожидания"
CLIENT_REVIEWS_TEXT = "⭐ Отзывы"


def client_main_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text=CLIENT_BOOK_TEXT),
                KeyboardButton(text=CLIENT_APPOINTMENTS_TEXT),
            ],
            [
                KeyboardButton(text=CLIENT_SERVICES_TEXT),
                KeyboardButton(text=CLIENT_CONTACTS_TEXT),
            ],
            [
                KeyboardButton(text=CLIENT_PORTFOLIO_TEXT),
                KeyboardButton(text=CLIENT_PREPARATION_TEXT),
            ],
            [
                KeyboardButton(text=CLIENT_WAITLIST_TEXT),
                KeyboardButton(text=CLIENT_NOTIFICATIONS_TEXT),
            ],
            [KeyboardButton(text=CLIENT_REVIEWS_TEXT)],
        ],
        resize_keyboard=True,
        input_field_placeholder="Выберите раздел",
    )
