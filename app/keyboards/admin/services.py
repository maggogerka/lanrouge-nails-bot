"""Inline and reply keyboards for service catalog management."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.schemas.service import ServiceAddonView, ServiceView

CANCEL_TEXT = "Отмена"


class ServiceCallback(CallbackData, prefix="svc"):
    """Compact service catalog callback payload."""

    action: str
    service_id: int
    page: int = 1


class ServiceAddonAdminCallback(CallbackData, prefix="svca"):
    action: str
    service_id: int
    addon_id: int = 0


def service_list_keyboard(
    services: list[ServiceView],
    *,
    include_archived: bool = False,
    page: int = 1,
    pages: int = 1,
) -> InlineKeyboardMarkup:
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
                        page=page,
                    ).pack(),
                )
            ],
        )
    list_action = "list_archived" if include_archived else "list"
    if pages > 1:
        navigation: list[InlineKeyboardButton] = []
        if page > 1:
            navigation.append(
                InlineKeyboardButton(
                    text="◀️",
                    callback_data=ServiceCallback(
                        action=list_action, service_id=0, page=page - 1
                    ).pack(),
                )
            )
        navigation.append(
            InlineKeyboardButton(
                text=f"{page}/{pages}",
                callback_data=ServiceCallback(action=list_action, service_id=0, page=page).pack(),
            )
        )
        if page < pages:
            navigation.append(
                InlineKeyboardButton(
                    text="▶️",
                    callback_data=ServiceCallback(
                        action=list_action, service_id=0, page=page + 1
                    ).pack(),
                )
            )
        rows.append(navigation)
    rows.append(
        [
            InlineKeyboardButton(
                text="🙈 Скрыть архив" if include_archived else "🗄 Показать архив",
                callback_data=ServiceCallback(
                    action="list" if include_archived else "list_archived",
                    service_id=0,
                    page=1,
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


def service_photo_keyboard(service: ServiceView) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="Добавить" if not service.telegram_photo_file_id else "Заменить",
                callback_data=ServiceCallback(action="photo_set", service_id=service.id).pack(),
            )
        ]
    ]
    if service.telegram_photo_file_id:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Удалить фотографию",
                    callback_data=ServiceCallback(
                        action="photo_delete", service_id=service.id
                    ).pack(),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="Назад к услуге",
                callback_data=ServiceCallback(action="view", service_id=service.id).pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def addon_list_keyboard(
    service_id: int,
    addons: list[ServiceAddonView],
    *,
    page: int = 1,
    pages: int = 1,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=("✅ " if addon.is_active else "⏸ ") + addon.name,
                callback_data=ServiceAddonAdminCallback(
                    action="view", service_id=service_id, addon_id=addon.id
                ).pack(),
            )
        ]
        for addon in addons
    ]
    if pages > 1:
        navigation: list[InlineKeyboardButton] = []
        if page > 1:
            navigation.append(
                InlineKeyboardButton(
                    text="◀️",
                    callback_data=ServiceCallback(
                        action="addons", service_id=service_id, page=page - 1
                    ).pack(),
                )
            )
        navigation.append(
            InlineKeyboardButton(
                text=f"{page}/{pages}",
                callback_data=ServiceCallback(
                    action="addons", service_id=service_id, page=page
                ).pack(),
            )
        )
        if page < pages:
            navigation.append(
                InlineKeyboardButton(
                    text="▶️",
                    callback_data=ServiceCallback(
                        action="addons", service_id=service_id, page=page + 1
                    ).pack(),
                )
            )
        rows.append(navigation)
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="Добавить допуслугу",
                    callback_data=ServiceAddonAdminCallback(
                        action="add", service_id=service_id
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Назад к услуге",
                    callback_data=ServiceCallback(action="view", service_id=service_id).pack(),
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def addon_details_keyboard(addon: ServiceAddonView) -> InlineKeyboardMarkup:
    lifecycle_action = "archive" if addon.is_active else "activate"
    lifecycle_text = "Скрыть" if addon.is_active else "Активировать"
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Название",
                    callback_data=ServiceAddonAdminCallback(
                        action="edit_name",
                        service_id=addon.service_id,
                        addon_id=addon.id,
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="Описание",
                    callback_data=ServiceAddonAdminCallback(
                        action="edit_description",
                        service_id=addon.service_id,
                        addon_id=addon.id,
                    ).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Цена",
                    callback_data=ServiceAddonAdminCallback(
                        action="edit_price",
                        service_id=addon.service_id,
                        addon_id=addon.id,
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="Длительность",
                    callback_data=ServiceAddonAdminCallback(
                        action="edit_duration",
                        service_id=addon.service_id,
                        addon_id=addon.id,
                    ).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Фотография",
                    callback_data=ServiceAddonAdminCallback(
                        action="photo_preview",
                        service_id=addon.service_id,
                        addon_id=addon.id,
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text=lifecycle_text,
                    callback_data=ServiceAddonAdminCallback(
                        action=lifecycle_action,
                        service_id=addon.service_id,
                        addon_id=addon.id,
                    ).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="К списку допуслуг",
                    callback_data=ServiceCallback(
                        action="addons", service_id=addon.service_id
                    ).pack(),
                )
            ],
        ]
    )


def addon_photo_keyboard(addon: ServiceAddonView) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="Добавить" if not addon.telegram_photo_file_id else "Заменить",
                callback_data=ServiceAddonAdminCallback(
                    action="photo_set",
                    service_id=addon.service_id,
                    addon_id=addon.id,
                ).pack(),
            )
        ]
    ]
    if addon.telegram_photo_file_id:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Удалить фотографию",
                    callback_data=ServiceAddonAdminCallback(
                        action="photo_delete",
                        service_id=addon.service_id,
                        addon_id=addon.id,
                    ).pack(),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="Назад",
                callback_data=ServiceAddonAdminCallback(
                    action="view",
                    service_id=addon.service_id,
                    addon_id=addon.id,
                ).pack(),
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
                    text="📷 Фотография",
                    callback_data=ServiceCallback(
                        action="photo_preview", service_id=service.id
                    ).pack(),
                ),
                InlineKeyboardButton(
                    text="➕ Допуслуги",
                    callback_data=ServiceCallback(action="addons", service_id=service.id).pack(),
                ),
            ],
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
                    text="Удалить, если не использовалась",
                    callback_data=ServiceCallback(
                        action="delete_confirm",
                        service_id=service_id,
                    ).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔥 Удалить принудительно",
                    callback_data=ServiceCallback(
                        action="force_delete_prompt",
                        service_id=service_id,
                    ).pack(),
                ),
            ],
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=ServiceCallback(
                        action="view",
                        service_id=service_id,
                    ).pack(),
                ),
            ],
        ]
    )


def force_delete_confirmation_keyboard(service_id: int) -> InlineKeyboardMarkup:
    """Require a separate final confirmation for aggregate deletion."""

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔥 Да, удалить безвозвратно",
                    callback_data=ServiceCallback(
                        action="force_delete_confirm",
                        service_id=service_id,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Нет, вернуться",
                    callback_data=ServiceCallback(
                        action="view",
                        service_id=service_id,
                    ).pack(),
                )
            ],
        ]
    )


def cancel_keyboard() -> ReplyKeyboardMarkup:
    """Offer a consistent escape from admin FSM forms."""

    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=CANCEL_TEXT)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
