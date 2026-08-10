"""View and edit core business rules through one-value FSM forms."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from pydantic import ValidationError

from app.handlers.admin.service_common import actor_from_telegram
from app.keyboards.admin.main import ADMIN_SETTINGS_TEXT, admin_main_keyboard
from app.keyboards.admin.services import cancel_keyboard
from app.keyboards.admin.settings import SettingsCallback, settings_keyboard
from app.schemas.service import AdminActor
from app.schemas.settings import BusinessSettingsPatch, BusinessSettingsView
from app.services.menu_service import MenuService
from app.services.settings_service import SettingsService
from app.states.admin_settings import AdminSettingsEdit

router = Router(name="admin.settings")

_PROMPTS = {
    "max_appointments_per_day": "Введите максимальное число записей на день (1–20):",
    "booking_horizon_days": "Введите горизонт записи в днях (1–365):",
    "cancellation_deadline_hours": "Введите дедлайн отмены в часах (1–720):",
    "default_window_duration_minutes": "Введите длительность окна по умолчанию в минутах:",
    "minimum_gap_minutes": "Введите минимальный интервал в минутах (можно 0):",
    "reminder_offsets_minutes": "Введите offsets напоминаний в минутах через запятую:",
}


def render_settings(settings: BusinessSettingsView) -> str:
    return (
        "<b>Основные настройки</b>\n"
        f"Часовой пояс: {settings.timezone}\n"
        f"Горизонт: {settings.booking_horizon_days} дн.\n"
        f"Дедлайн отмены/переноса: {settings.cancellation_deadline_hours} ч.\n"
        f"Лимит записей на день: {settings.max_appointments_per_day}\n"
        f"Окно по умолчанию: {settings.default_window_duration_minutes} мин.\n"
        f"Минимальный интервал: {settings.minimum_gap_minutes} мин.\n"
        f"Суббота: {'разрешена' if settings.allow_saturday else 'закрыта'}\n"
        f"Воскресенье: {'разрешено' if settings.allow_sunday else 'закрыто'}\n"
        f"Отзывы: {'включены' if settings.reviews_enabled else 'выключены'}\n"
        "Напоминания: "
        + ", ".join(str(value) for value in settings.reminder_offsets_minutes)
        + f" мин.\nВерсия настроек: {settings.version}"
    )


async def _show_settings_message(
    message: Message,
    service: SettingsService,
    actor: AdminActor,
) -> None:
    settings = await service.get(actor)
    await message.answer(render_settings(settings), reply_markup=settings_keyboard(settings))


@router.message(F.text == ADMIN_SETTINGS_TEXT)
async def show_settings(message: Message, settings_service: SettingsService) -> None:
    if message.from_user is None:
        return
    await _show_settings_message(
        message,
        settings_service,
        actor_from_telegram(message.from_user),
    )


@router.callback_query(SettingsCallback.filter(F.action == "view"))
async def refresh_settings(
    callback: CallbackQuery,
    settings_service: SettingsService,
) -> None:
    settings = await settings_service.get(actor_from_telegram(callback.from_user))
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            render_settings(settings),
            reply_markup=settings_keyboard(settings),
        )
    await callback.answer()


@router.callback_query(SettingsCallback.filter(F.action.in_(set(_PROMPTS))))
async def begin_setting_edit(
    callback: CallbackQuery,
    callback_data: SettingsCallback,
    state: FSMContext,
) -> None:
    await state.update_data(setting_field=callback_data.action)
    await state.set_state(AdminSettingsEdit.value)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            _PROMPTS[callback_data.action],
            reply_markup=cancel_keyboard(),
        )
    await callback.answer()


@router.message(AdminSettingsEdit.value)
async def save_setting(
    message: Message,
    state: FSMContext,
    settings_service: SettingsService,
    correlation_id: str,
    menu_service: MenuService,
) -> None:
    if message.from_user is None:
        return
    data = await state.get_data()
    field = str(data.get("setting_field", ""))
    raw = (message.text or "").strip()
    try:
        value: object = (
            [int(item.strip()) for item in raw.split(",") if item.strip()]
            if field == "reminder_offsets_minutes"
            else int(raw)
        )
        patch = BusinessSettingsPatch.model_validate({field: value})
        settings = await settings_service.update(
            actor_from_telegram(message.from_user),
            patch,
            correlation_id=correlation_id,
        )
    except (ValidationError, ValueError) as exc:
        await message.answer(f"Некорректное значение: {exc}")
        return
    await state.clear()
    await message.answer(
        render_settings(settings),
        reply_markup=settings_keyboard(settings),
    )
    await message.answer(
        "Настройка сохранена.",
        reply_markup=admin_main_keyboard(await menu_service.get_capabilities()),
    )


@router.callback_query(SettingsCallback.filter(F.action.in_({"toggle_saturday", "toggle_sunday"})))
async def toggle_weekend(
    callback: CallbackQuery,
    callback_data: SettingsCallback,
    settings_service: SettingsService,
    correlation_id: str,
) -> None:
    actor = actor_from_telegram(callback.from_user)
    current = await settings_service.get(actor)
    patch = (
        BusinessSettingsPatch(allow_saturday=not current.allow_saturday)
        if callback_data.action == "toggle_saturday"
        else BusinessSettingsPatch(allow_sunday=not current.allow_sunday)
    )
    settings = await settings_service.update(actor, patch, correlation_id=correlation_id)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            render_settings(settings),
            reply_markup=settings_keyboard(settings),
        )
    await callback.answer("Настройка обновлена.")


@router.callback_query(SettingsCallback.filter(F.action == "toggle_broadcasts"))
async def toggle_broadcasts(
    callback: CallbackQuery,
    settings_service: SettingsService,
    correlation_id: str,
) -> None:
    actor = actor_from_telegram(callback.from_user)
    current = await settings_service.get(actor)
    settings = await settings_service.update(
        actor,
        BusinessSettingsPatch(broadcasts_enabled=not current.broadcasts_enabled),
        correlation_id=correlation_id,
    )
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            render_settings(settings), reply_markup=settings_keyboard(settings)
        )
    await callback.answer("Настройка рассылок обновлена.")


@router.callback_query(SettingsCallback.filter(F.action == "toggle_reviews"))
async def toggle_reviews(
    callback: CallbackQuery,
    settings_service: SettingsService,
    correlation_id: str,
) -> None:
    actor = actor_from_telegram(callback.from_user)
    current = await settings_service.get(actor)
    settings = await settings_service.update(
        actor,
        BusinessSettingsPatch(reviews_enabled=not current.reviews_enabled),
        correlation_id=correlation_id,
    )
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            render_settings(settings), reply_markup=settings_keyboard(settings)
        )
    await callback.answer(
        "Отзывы включены." if settings.reviews_enabled else "Отзывы полностью отключены."
    )
