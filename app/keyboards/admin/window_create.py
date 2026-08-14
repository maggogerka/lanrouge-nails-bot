"""Inline actions for the confirmed availability-window draft."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.schemas.authorization import StaffMemberView
from app.schemas.service import ServiceView


class WindowFormCallback(CallbackData, prefix="awf"):
    action: str
    value: str = "-"


def window_master_keyboard(masters: tuple[StaffMemberView, ...]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"💅 {master.display_name[:35]}",
                    callback_data=WindowFormCallback(
                        action="master",
                        value=str(master.id),
                    ).pack(),
                )
            ]
            for master in masters
        ]
        + [[_cancel_button()]]
    )


def window_service_keyboard(services: list[ServiceView]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🛠 {service.name[:42]}",
                    callback_data=WindowFormCallback(
                        action="service",
                        value=str(service.id),
                    ).pack(),
                )
            ]
            for service in services
        ]
        + [[_cancel_button()]]
    )


def duration_keyboard(default_minutes: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"✅ По умолчанию ({_duration_label(default_minutes)})",
                    callback_data=WindowFormCallback(action="duration_default").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Ввести длительность",
                    callback_data=WindowFormCallback(action="duration_manual").pack(),
                )
            ],
            [_cancel_button()],
        ]
    )


def comment_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⏭ Без комментария",
                    callback_data=WindowFormCallback(action="comment_skip").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Ввести комментарий",
                    callback_data=WindowFormCallback(action="comment_manual").pack(),
                )
            ],
            [_cancel_button()],
        ]
    )


def window_confirmation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Создать окно",
                    callback_data=WindowFormCallback(action="create").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Изменить дату",
                    callback_data=WindowFormCallback(action="edit_date").pack(),
                ),
                InlineKeyboardButton(
                    text="✏️ Изменить время",
                    callback_data=WindowFormCallback(action="edit_time").pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Изменить длительность",
                    callback_data=WindowFormCallback(action="edit_duration").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="✏️ Изменить комментарий",
                    callback_data=WindowFormCallback(action="edit_comment").pack(),
                )
            ],
            [_cancel_button()],
        ]
    )


def window_created_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Добавить ещё время на эту дату",
                    callback_data=WindowFormCallback(action="another_same").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="📅 Добавить окно на другую дату",
                    callback_data=WindowFormCallback(action="another_date").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🕒 Посмотреть открытые окна",
                    callback_data=WindowFormCallback(action="list").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Завершить",
                    callback_data=WindowFormCallback(action="done").pack(),
                )
            ],
        ]
    )


def _cancel_button() -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text="❌ Отмена",
        callback_data=WindowFormCallback(action="cancel").pack(),
    )


def _duration_label(minutes: int) -> str:
    hours, remainder = divmod(minutes, 60)
    if hours and remainder:
        return f"{hours} ч {remainder} мин"
    if hours:
        return f"{hours} ч"
    return f"{remainder} мин"
