"""Client main menu containing only usable or gracefully deferred sections."""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from app.schemas.menu import MenuCapabilities

CLIENT_BOOK_TEXT = "✨ Записаться"
CLIENT_APPOINTMENTS_TEXT = "📅 Мои записи"
CLIENT_SERVICES_TEXT = "💵 Услуги и цены"
CLIENT_MASTERS_TEXT = "👩‍💼 Мастера"
CLIENT_SUPPORT_TEXT = "🛟 Поддержка и контакты"
CLIENT_CONTACTS_TEXT = CLIENT_SUPPORT_TEXT
CLIENT_PORTFOLIO_TEXT = "🖼 Портфолио"
CLIENT_NOTIFICATIONS_TEXT = "🔔 Настройки уведомлений"
CLIENT_WAITLIST_TEXT = "⏳ Лист ожидания"
CLIENT_REVIEWS_TEXT = "⭐ Отзывы"
CLIENT_REPEAT_TEXT = "🔁 Повторить запись"
CLIENT_PRIVACY_TEXT = "🔐 Приватность"


CLIENT_MASTER_PROFILE_TEXT = "ℹ️ О мастере"


def client_main_keyboard(capabilities: MenuCapabilities | None = None) -> ReplyKeyboardMarkup:
    visible = capabilities or MenuCapabilities()
    rows: list[list[KeyboardButton]] = []

    primary = []
    if visible.online_booking_visible:
        primary.append(KeyboardButton(text=CLIENT_BOOK_TEXT))
    if visible.appointments_visible:
        primary.append(KeyboardButton(text=CLIENT_APPOINTMENTS_TEXT))
    if primary:
        rows.append(primary)

    catalog = []
    if visible.services_visible:
        catalog.append(KeyboardButton(text=CLIENT_SERVICES_TEXT))
    if visible.masters_visible:
        catalog.append(KeyboardButton(text=CLIENT_MASTERS_TEXT))
    if catalog:
        rows.append(catalog)

    social = []
    if visible.portfolio_visible:
        social.append(KeyboardButton(text=CLIENT_PORTFOLIO_TEXT))
    if visible.reviews_visible:
        social.append(KeyboardButton(text=CLIENT_REVIEWS_TEXT))
    if social:
        rows.append(social)

    preferences = []
    if visible.waitlist_visible:
        preferences.append(KeyboardButton(text=CLIENT_WAITLIST_TEXT))
    if visible.notifications_visible:
        preferences.append(KeyboardButton(text=CLIENT_NOTIFICATIONS_TEXT))
    if preferences:
        rows.append(preferences)

    follow_up = []
    if visible.repeat_booking_visible:
        follow_up.append(KeyboardButton(text=CLIENT_REPEAT_TEXT))
    if visible.support_visible:
        follow_up.append(KeyboardButton(text=CLIENT_SUPPORT_TEXT))
    if follow_up:
        rows.append(follow_up)

    if visible.privacy_visible:
        rows.append([KeyboardButton(text=CLIENT_PRIVACY_TEXT)])
    if visible.master_profile_visible and not visible.masters_visible:
        rows.append([KeyboardButton(text=CLIENT_MASTER_PROFILE_TEXT)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder="Выберите раздел",
    )
