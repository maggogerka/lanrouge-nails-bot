"""Inline controls for mutable business rule settings."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.schemas.settings import BusinessSettingsView


class SettingsCallback(CallbackData, prefix="set"):
    action: str


def settings_keyboard(settings: BusinessSettingsView) -> InlineKeyboardMarkup:
    labels = [
        (
            f"📊 Лимит записей в день · {settings.max_appointments_per_day}",
            "max_appointments_per_day",
        ),
        (f"📅 Горизонт записи · {settings.booking_horizon_days} дн.", "booking_horizon_days"),
        (
            f"❌ Отмена не позднее · {settings.cancellation_deadline_hours} ч.",
            "cancellation_deadline_hours",
        ),
        (
            f"🔄 Перенос не позднее · {settings.reschedule_deadline_hours} ч.",
            "reschedule_deadline_hours",
        ),
        (
            f"⏱ Окно по умолчанию · {settings.default_window_duration_minutes} мин.",
            "default_window_duration_minutes",
        ),
        (f"↔️ Интервал между окнами · {settings.minimum_gap_minutes} мин.", "minimum_gap_minutes"),
        ("🔔 Настроить напоминания", "reminder_offsets_minutes"),
        (f"🛡 Лимит клиента · {settings.future_booking_limit_max}", "future_booking_limit_max"),
        (
            f"🗓 Горизонт антиспама · {settings.future_booking_limit_horizon_days} дн.",
            "future_booking_limit_horizon_days",
        ),
    ]
    rows = [
        [
            InlineKeyboardButton(
                text=label,
                callback_data=SettingsCallback(action=field).pack(),
            )
        ]
        for label, field in labels
    ]
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=(
                        "✅ Антиспам включён"
                        if settings.future_booking_limit_enabled
                        else "⛔ Антиспам выключен"
                    ),
                    callback_data=SettingsCallback(action="toggle_future_limit").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text=(
                        "✅ Учитывать отмены клиента"
                        if settings.future_booking_count_client_cancellations
                        else "⛔ Не учитывать отмены клиента"
                    ),
                    callback_data=SettingsCallback(action="toggle_future_cancellations").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text=(
                        "✅ Отзывы включены" if settings.reviews_enabled else "⛔ Отзывы выключены"
                    ),
                    callback_data=SettingsCallback(action="toggle_reviews").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text=(
                        "✅ Рассылки включены"
                        if settings.broadcasts_enabled
                        else "⛔ Рассылки выключены"
                    ),
                    callback_data=SettingsCallback(action="toggle_broadcasts").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text=("✅ Суббота" if settings.allow_saturday else "⛔ Суббота"),
                    callback_data=SettingsCallback(action="toggle_saturday").pack(),
                ),
                InlineKeyboardButton(
                    text=("✅ Воскресенье" if settings.allow_sunday else "⛔ Воскресенье"),
                    callback_data=SettingsCallback(action="toggle_sunday").pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Обновить настройки",
                    callback_data=SettingsCallback(action="view").pack(),
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
