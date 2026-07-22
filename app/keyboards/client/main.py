"""Client main menu containing only usable or gracefully deferred sections."""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from app.schemas.menu import MenuCapabilities

CLIENT_BOOK_TEXT = "💅 Записаться"
CLIENT_APPOINTMENTS_TEXT = "📅 Моя запись"
CLIENT_SERVICES_TEXT = "💵 Услуги и цены"
CLIENT_CONTACTS_TEXT = "📍 Адрес и контакты"
CLIENT_PORTFOLIO_TEXT = "🖼 Работы мастера"
CLIENT_PREPARATION_TEXT = "❓ Подготовка к процедуре"
CLIENT_NOTIFICATIONS_TEXT = "🔔 Настройки уведомлений"
CLIENT_WAITLIST_TEXT = "⏳ Лист ожидания"
CLIENT_REVIEWS_TEXT = "⭐ Отзывы"
CLIENT_REPEAT_TEXT = "🔁 Повторить запись"


CLIENT_MASTER_PROFILE_TEXT = "ℹ️ О мастере"


def client_main_keyboard(capabilities: MenuCapabilities | None = None) -> ReplyKeyboardMarkup:
    portfolio_visible = capabilities is None or capabilities.portfolio_visible
    reviews_visible = capabilities is None or capabilities.reviews_visible
    master_profile_visible = capabilities is None or capabilities.master_profile_visible
    rows = [
        [
            KeyboardButton(text=CLIENT_BOOK_TEXT),
            KeyboardButton(text=CLIENT_APPOINTMENTS_TEXT),
        ],
        [
            KeyboardButton(text=CLIENT_SERVICES_TEXT),
            KeyboardButton(text=CLIENT_CONTACTS_TEXT),
        ],
    ]
    if portfolio_visible:
        rows.append([KeyboardButton(text=CLIENT_PORTFOLIO_TEXT)])
    rows.append(
        [
            KeyboardButton(text=CLIENT_WAITLIST_TEXT),
            KeyboardButton(text=CLIENT_NOTIFICATIONS_TEXT),
        ]
    )
    final_row = [KeyboardButton(text=CLIENT_REPEAT_TEXT)]
    if reviews_visible:
        final_row.append(KeyboardButton(text=CLIENT_REVIEWS_TEXT))
    rows.append(final_row)
    if master_profile_visible:
        rows.append([KeyboardButton(text=CLIENT_MASTER_PROFILE_TEXT)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder="Выберите раздел",
    )
