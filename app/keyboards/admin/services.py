"""Inline and reply keyboards for service catalog management."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.schemas.service import ServiceView

CANCEL_TEXT = "Отмена"


class ServiceCallback(CallbackData, prefix="svc"):
    """Compact service catalog callback payload."""

    action: str
    service_id: int


def service_list_keyboard(services: list[ServiceView]) -> InlineKeyboardMarkup:
    """Show all services and an add action."""

    rows: list[list[InlineKeyboardButton]] = []
    for service in services:
        marker = "✅" if service.is_active else "⏸"
        label = f"{marker} {service.name}"[:60]
        rows.append(
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=ServiceCallback(
                        action="view",
                        service_id=service.id,
                    ).pack(),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="➕ Добавить услугу",
                callback_data=ServiceCallback(action="add", service_id=0).pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def service_details_keyboard(service: ServiceView) -> InlineKeyboardMarkup:
    """Return field edits and lifecycle actions for one service."""

    lifecycle_action = "archive" if service.is_active else "activate"
    lifecycle_text = "⏸ Скрыть" if service.is_active else "▶️ Активировать"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✏️ Название",
                    callback_data=ServiceCallback(
                        action="edit_name",
                        service_id=service.id,
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="📝 Описание",
                    callback_data=ServiceCallback(
                        action="edit_description",
                        service_id=service.id,
                    ).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💳 Предоплата",
                    callback_data=ServiceCallback(
                        action="edit_prepayment",
                        service_id=service.id,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="💵 Цена",
                    callback_data=ServiceCallback(
                        action="edit_price",
                        service_id=service.id,
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="⏱ Длительность",
                    callback_data=ServiceCallback(
                        action="edit_duration",
                        service_id=service.id,
                    ).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text=lifecycle_text,
                    callback_data=ServiceCallback(
                        action=lifecycle_action,
                        service_id=service.id,
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="🗑 Удалить",
                    callback_data=ServiceCallback(
                        action="delete_prompt",
                        service_id=service.id,
                    ).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ К списку",
                    callback_data=ServiceCallback(action="list", service_id=0).pack(),
                )
            ],
        ]
    )


def delete_confirmation_keyboard(service_id: int) -> InlineKeyboardMarkup:
    """Require an explicit second click before physical deletion."""

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, удалить",
                    callback_data=ServiceCallback(
                        action="delete_confirm",
                        service_id=service_id,
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="Нет",
                    callback_data=ServiceCallback(
                        action="view",
                        service_id=service_id,
                    ).pack(),
                ),
            ]
        ]
    )


def cancel_keyboard() -> ReplyKeyboardMarkup:
    """Offer a consistent escape from admin FSM forms."""

    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=CANCEL_TEXT)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
