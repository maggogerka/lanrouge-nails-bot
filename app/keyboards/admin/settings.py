"""Inline controls for mutable business rule settings."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.schemas.settings import BusinessSettingsView


class SettingsCallback(CallbackData, prefix="set"):
    action: str


def settings_keyboard(settings: BusinessSettingsView) -> InlineKeyboardMarkup:
    labels = [
        ("Лимит записей", "max_appointments_per_day"),
        ("Горизонт записи", "booking_horizon_days"),
        ("Дедлайн отмены", "cancellation_deadline_hours"),
        ("Длительность окна", "default_window_duration_minutes"),
        ("Интервал между окнами", "minimum_gap_minutes"),
        ("Напоминания", "reminder_offsets_minutes"),
        ("Лимит будущих записей", "future_booking_limit_max"),
        ("Горизонт антиспама", "future_booking_limit_horizon_days"),
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
                    text="Обновить",
                    callback_data=SettingsCallback(action="view").pack(),
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
