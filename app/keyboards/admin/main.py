"""Administrative reply menu."""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from app.domain.enums import StaffRole
from app.schemas.authorization import StaffContext, StaffPermission
from app.schemas.menu import MenuCapabilities
from app.security import get_staff_context

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
ADMIN_PRIVACY_TEXT = "🛡 Запросы на удаление"
ADMIN_FEATURES_TEXT = "🧩 Функции бота"
ADMIN_STAFF_TEXT = "👩‍💼 Мастера и сотрудники"
ADMIN_PAYMENTS_TEXT = "💳 Оплата"
ADMIN_STATISTICS_TEXT = "📊 Статистика"
ADMIN_VENDOR_SUPPORT_TEXT = "🛟 Техническая поддержка"
ADMIN_BUSINESS_SETTINGS_TEXT = "🏢 Настройки бизнеса"


ADMIN_MASTER_PROFILE_TEXT = "ℹ️ Информация о мастере"


def admin_main_keyboard(
    capabilities: MenuCapabilities | None = None,
    staff_context: StaffContext | None = None,
) -> ReplyKeyboardMarkup:
    """Return only implemented administrative sections."""

    visible = capabilities or MenuCapabilities()
    context = staff_context or get_staff_context()
    rows: list[list[KeyboardButton]] = []
    if _can(context, StaffPermission.MANAGE_ALL_APPOINTMENTS):
        rows.append(
            [
                KeyboardButton(text=ADMIN_TODAY_TEXT),
                KeyboardButton(text=ADMIN_UPCOMING_TEXT),
            ]
        )
    if _can(context, StaffPermission.MANAGE_ALL_SCHEDULES):
        rows.append(
            [
                KeyboardButton(text=ADMIN_ADD_WINDOW_TEXT),
                KeyboardButton(text=ADMIN_WINDOWS_TEXT),
            ]
        )
    management = []
    if _can(context, StaffPermission.MANAGE_SERVICES):
        management.append(KeyboardButton(text=ADMIN_SERVICES_TEXT))
    if _can(context, StaffPermission.MANAGE_ALL_CLIENTS):
        management.append(KeyboardButton(text=ADMIN_CLIENTS_TEXT))
    if management:
        rows.append(management)
    optional = []
    if visible.portfolio_visible and _can(context, StaffPermission.MANAGE_SERVICES):
        optional.append(KeyboardButton(text=ADMIN_PORTFOLIO_TEXT))
    if visible.waitlist_visible and _can(context, StaffPermission.MANAGE_ALL_APPOINTMENTS):
        optional.append(KeyboardButton(text=ADMIN_WAITLIST_TEXT))
    if optional:
        rows.append(optional)
    if visible.reviews_visible and _can(context, StaffPermission.MANAGE_ALL_CLIENTS):
        rows.append([KeyboardButton(text=ADMIN_REVIEWS_TEXT)])
    if visible.broadcasts_visible and _can(context, StaffPermission.MANAGE_BROADCASTS):
        rows.append([KeyboardButton(text=ADMIN_BROADCASTS_TEXT)])
    if _can(context, StaffPermission.HANDLE_DATA_DELETION):
        rows.append([KeyboardButton(text=ADMIN_PRIVACY_TEXT)])
    if _can(context, StaffPermission.VIEW_STAFF):
        rows.append([KeyboardButton(text=ADMIN_STAFF_TEXT)])
    if _can(context, StaffPermission.VIEW_PAYMENTS):
        rows.append([KeyboardButton(text=ADMIN_PAYMENTS_TEXT)])
    if _can(context, StaffPermission.VIEW_FEATURE_FLAGS):
        rows.append([KeyboardButton(text=ADMIN_FEATURES_TEXT)])
    if _can(context, StaffPermission.VIEW_ALL_STATISTICS):
        rows.append([KeyboardButton(text=ADMIN_STATISTICS_TEXT)])
    if _can(context, StaffPermission.MANAGE_BUSINESS):
        rows.append([KeyboardButton(text=ADMIN_BUSINESS_SETTINGS_TEXT)])
        rows.append([KeyboardButton(text=ADMIN_MASTER_PROFILE_TEXT)])
    if _can(context, StaffPermission.MANAGE_PRIVATE_SETTINGS):
        rows.append([KeyboardButton(text=ADMIN_SETTINGS_TEXT)])
    if (
        context is not None
        and context.role in {StaffRole.OWNER, StaffRole.MANAGER}
        and _can(context, StaffPermission.VIEW_VENDOR_SUPPORT)
    ):
        rows.append([KeyboardButton(text=ADMIN_VENDOR_SUPPORT_TEXT)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder="Меню администратора",
    )


def _can(context: StaffContext | None, permission: StaffPermission) -> bool:
    return context is not None and context.has_permission(permission)
