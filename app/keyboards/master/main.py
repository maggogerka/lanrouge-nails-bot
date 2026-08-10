"""Safe, self-scoped master menu."""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup

from app.schemas.authorization import StaffContext, StaffPermission
from app.security import get_staff_context

MASTER_APPOINTMENTS_TEXT = "📅 Мои записи"
MASTER_SCHEDULE_TEXT = "🕒 Моё расписание"
MASTER_SUPPORT_TEXT = "🛟 Поддержка"


def master_main_keyboard(staff_context: StaffContext | None = None) -> ReplyKeyboardMarkup:
    """Expose only master-owned sections, never legacy admin CRUD."""

    context = staff_context or get_staff_context()
    rows: list[list[KeyboardButton]] = []
    if _can(context, StaffPermission.VIEW_OWN_APPOINTMENTS):
        rows.append([KeyboardButton(text=MASTER_APPOINTMENTS_TEXT)])
    if _can(context, StaffPermission.VIEW_OWN_SCHEDULE):
        rows.append([KeyboardButton(text=MASTER_SCHEDULE_TEXT)])
    if _can(context, StaffPermission.VIEW_VENDOR_SUPPORT):
        rows.append([KeyboardButton(text=MASTER_SUPPORT_TEXT)])
    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder="Меню мастера",
    )


def _can(context: StaffContext | None, permission: StaffPermission) -> bool:
    return context is not None and context.has_permission(permission)
