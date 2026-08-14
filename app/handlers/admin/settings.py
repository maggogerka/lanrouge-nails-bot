"""View and edit core business rules through one-value FSM forms."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from pydantic import ValidationError

from app.handlers.admin.service_common import actor_from_telegram
from app.keyboards.admin.main import ADMIN_SETTINGS_TEXT, admin_main_keyboard
from app.keyboards.admin.services import cancel_keyboard
from app.keyboards.admin.settings import (
    SettingsCallback,
    reminder_settings_keyboard,
    settings_keyboard,
)
from app.schemas.service import AdminActor
from app.schemas.settings import BusinessSettingsPatch, BusinessSettingsView
from app.services.menu_service import MenuService
from app.services.settings_service import SettingsService
from app.states.admin_settings import AdminSettingsEdit

router = Router(name="admin.settings")

_PROMPTS = {
    "max_appointments_per_day": "Введите максимальное число записей на день (1–20):",
    "booking_horizon_days": "Введите горизонт записи в днях (1–365):",
    "cancellation_deadline_hours": (
        "За сколько часов до визита клиент уже не сможет отменить запись? "
        "Введите число от 1 до 720, например 24:"
    ),
    "reschedule_deadline_hours": (
        "За сколько часов до визита клиент уже не сможет перенести запись? "
        "Введите число от 1 до 720, например 24:"
    ),
    "default_window_duration_minutes": "Введите длительность окна по умолчанию в минутах:",
    "minimum_gap_minutes": "Введите минимальный интервал в минутах (можно 0):",
    "future_booking_limit_max": "Введите максимум будущих записей одного клиента (1–100):",
    "future_booking_limit_horizon_days": "Введите горизонт лимита в днях (1–365):",
}


def render_settings(settings: BusinessSettingsView) -> str:
    cancellation_policy = (
        "учитываются" if settings.future_booking_count_client_cancellations else "не учитываются"
    )
    return (
        "<b>Основные настройки</b>\n"
        f"Часовой пояс: {settings.timezone}\n"
        f"Горизонт: {settings.booking_horizon_days} дн.\n"
        f"Дедлайн отмены: {settings.cancellation_deadline_hours} ч. до визита\n"
        f"Дедлайн переноса: {settings.reschedule_deadline_hours} ч. до визита\n"
        f"Лимит записей на день: {settings.max_appointments_per_day}\n"
        "Антиспам будущих записей: "
        f"{'включён' if settings.future_booking_limit_enabled else 'выключен'}\n"
        f"Лимит клиента: {settings.future_booking_limit_max} "
        f"за {settings.future_booking_limit_horizon_days} дн.\n"
        f"Отмены клиента в лимите: {cancellation_policy}\n"
        f"Окно по умолчанию: {settings.default_window_duration_minutes} мин.\n"
        f"Минимальный интервал: {settings.minimum_gap_minutes} мин.\n"
        f"Суббота: {'разрешена' if settings.allow_saturday else 'закрыта'}\n"
        f"Воскресенье: {'разрешено' if settings.allow_sunday else 'закрыто'}\n"
        "Напоминания о записи: "
        + _format_reminders(settings.reminder_offsets_minutes)
        + f"\nВерсия настроек: {settings.version}"
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


@router.callback_query(SettingsCallback.filter(F.action == "reminders"))
async def show_reminder_settings(
    callback: CallbackQuery,
    settings_service: SettingsService,
) -> None:
    settings = await settings_service.get(actor_from_telegram(callback.from_user))
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "<b>Напоминания о предстоящей записи</b>\n\n"
            "Бот отправляет их клиенту и активным сотрудникам с административным доступом. "
            "Каждое значение означает, за сколько времени до начала визита придёт сообщение.\n\n"
            f"Сейчас: {_format_reminders(settings.reminder_offsets_minutes)}\n\n"
            "Выберите готовый вариант или задайте до пяти своих интервалов. "
            "Отправка работает, когда «Напоминания» включены в разделе «Функции бота».",
            reply_markup=reminder_settings_keyboard(settings),
        )
    await callback.answer()


_REMINDER_PRESETS = {
    "reminders_default": [1440, 180, 60],
    "reminders_day_two_hours": [1440, 120],
    "reminders_three_one": [180, 60],
}


@router.callback_query(SettingsCallback.filter(F.action.in_(set(_REMINDER_PRESETS))))
async def save_reminder_preset(
    callback: CallbackQuery,
    callback_data: SettingsCallback,
    settings_service: SettingsService,
    correlation_id: str,
) -> None:
    settings = await settings_service.update(
        actor_from_telegram(callback.from_user),
        BusinessSettingsPatch(reminder_offsets_minutes=_REMINDER_PRESETS[callback_data.action]),
        correlation_id=correlation_id,
    )
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Напоминания сохранены: " + _format_reminders(settings.reminder_offsets_minutes),
            reply_markup=reminder_settings_keyboard(settings),
        )
    await callback.answer("График напоминаний сохранён.")


@router.callback_query(SettingsCallback.filter(F.action == "reminders_custom"))
async def begin_custom_reminders(
    callback: CallbackQuery,
    state: FSMContext,
) -> None:
    await state.update_data(setting_field="reminder_offsets_minutes")
    await state.set_state(AdminSettingsEdit.value)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Введите от одного до пяти интервалов в минутах через запятую. "
            "Они отсчитываются назад от начала записи.\n\n"
            "Примеры:\n"
            "• <code>1440, 180, 60</code> — за 1 день, 3 часа и 1 час;\n"
            "• <code>120, 30</code> — за 2 часа и 30 минут.\n\n"
            "Допустимый интервал: от 1 минуты до 30 дней. Повторы нельзя.",
            reply_markup=cancel_keyboard(),
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
        if field == "reminder_offsets_minutes":
            await message.answer(
                "Не удалось сохранить график. Введите от 1 до 5 разных целых чисел "
                "через запятую: каждое от 1 до 43200 минут. Например: "
                "<code>1440, 180, 60</code>."
            )
        else:
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


@router.callback_query(
    SettingsCallback.filter(F.action.in_({"toggle_broadcasts", "toggle_reviews"}))
)
async def explain_moved_feature_toggle(
    callback: CallbackQuery,
) -> None:
    await callback.answer(
        "Переключатель перенесён в «Админ-панель → Функции бота». Обновите меню настроек.",
        show_alert=True,
    )


@router.callback_query(
    SettingsCallback.filter(F.action.in_({"toggle_future_limit", "toggle_future_cancellations"}))
)
async def toggle_future_booking_limit(
    callback: CallbackQuery,
    callback_data: SettingsCallback,
    settings_service: SettingsService,
    correlation_id: str,
) -> None:
    actor = actor_from_telegram(callback.from_user)
    current = await settings_service.get(actor)
    patch = (
        BusinessSettingsPatch(future_booking_limit_enabled=not current.future_booking_limit_enabled)
        if callback_data.action == "toggle_future_limit"
        else BusinessSettingsPatch(
            future_booking_count_client_cancellations=(
                not current.future_booking_count_client_cancellations
            )
        )
    )
    settings = await settings_service.update(actor, patch, correlation_id=correlation_id)
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            render_settings(settings), reply_markup=settings_keyboard(settings)
        )
    await callback.answer("Настройка антиспама обновлена.")


def _format_reminders(offsets: list[int]) -> str:
    def render(value: int) -> str:
        if value % 1440 == 0:
            return f"за {value // 1440} д."
        if value % 60 == 0:
            return f"за {value // 60} ч."
        return f"за {value} мин."

    return ", ".join(render(value) for value in sorted(offsets, reverse=True))
