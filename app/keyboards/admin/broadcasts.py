"""Broadcast builder, confirmation and result controls."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.domain.enums import BroadcastStatus
from app.schemas.broadcast import BroadcastView


class BroadcastCallback(CallbackData, prefix="bc"):
    action: str
    broadcast_id: int = 0
    value: str = ""


def broadcasts_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Создать рассылку", callback_data=BroadcastCallback(action="add").pack()
                )
            ],
            [
                InlineKeyboardButton(
                    text="📝 Черновики",
                    callback_data=BroadcastCallback(
                        action="list", value=BroadcastStatus.DRAFT.value
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⏰ Запланированные",
                    callback_data=BroadcastCallback(
                        action="list", value=BroadcastStatus.SCHEDULED.value
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🚀 Выполняющиеся",
                    callback_data=BroadcastCallback(
                        action="list", value=BroadcastStatus.SENDING.value
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="✅ Завершённые",
                    callback_data=BroadcastCallback(
                        action="list", value=BroadcastStatus.COMPLETED.value
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="📊 Все результаты", callback_data=BroadcastCallback(action="list").pack()
                )
            ],
        ]
    )


def media_done_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Готово / без фото",
                    callback_data=BroadcastCallback(action="media_done").pack(),
                )
            ]
        ]
    )


def audience_keyboard() -> InlineKeyboardMarkup:
    options = [
        ("Все подписанные", "all"),
        ("С выполненными визитами", "completed"),
        ("Без будущей записи", "without_future"),
        ("Не посещали 30 дней", "inactive"),
        ("По тегу", "tag"),
        ("По услуге", "service"),
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=text,
                    callback_data=BroadcastCallback(action="audience", value=value).pack(),
                )
            ]
            for text, value in options
        ]
    )


def button_type_keyboard() -> InlineKeyboardMarkup:
    options = [
        ("Без кнопки", "none"),
        ("Записаться", "book"),
        ("Посмотреть работы", "portfolio"),
        ("Свободные окна", "available_windows"),
        ("HTTPS-ссылка", "url"),
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=text, callback_data=BroadcastCallback(action="button", value=value).pack()
                )
            ]
            for text, value in options
        ]
    )


def preview_keyboard(broadcast_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Отправить тест",
                    callback_data=BroadcastCallback(
                        action="test", broadcast_id=broadcast_id
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Начать рассылку",
                    callback_data=BroadcastCallback(
                        action="launch", broadcast_id=broadcast_id
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Запланировать",
                    callback_data=BroadcastCallback(
                        action="schedule", broadcast_id=broadcast_id
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Изменить",
                    callback_data=BroadcastCallback(
                        action="edit", broadcast_id=broadcast_id
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=BroadcastCallback(
                        action="cancel", broadcast_id=broadcast_id
                    ).pack(),
                )
            ],
        ]
    )


def broadcast_list_keyboard(items: list[BroadcastView]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"#{item.id} · {item.title} · {item.status.value}",
                    callback_data=BroadcastCallback(action="result", broadcast_id=item.id).pack(),
                )
            ]
            for item in items
        ]
        + [
            [
                InlineKeyboardButton(
                    text="← Меню рассылок", callback_data=BroadcastCallback(action="menu").pack()
                )
            ]
        ]
    )


def result_keyboard(broadcast: BroadcastView) -> InlineKeyboardMarkup:
    rows = []
    if broadcast.status in {
        BroadcastStatus.DRAFT,
        BroadcastStatus.SCHEDULED,
        BroadcastStatus.PREPARING,
        BroadcastStatus.SENDING,
    }:
        rows.append(
            [
                InlineKeyboardButton(
                    text="Отменить рассылку",
                    callback_data=BroadcastCallback(
                        action="cancel", broadcast_id=broadcast.id
                    ).pack(),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="← К списку", callback_data=BroadcastCallback(action="list").pack()
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
