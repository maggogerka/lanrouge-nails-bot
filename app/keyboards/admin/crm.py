"""Inline controls for client lists, cards, tags and private notes."""

from __future__ import annotations

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.schemas.crm import ClientCardView, ClientSummaryView, ClientTagView


class CrmCallback(CallbackData, prefix="crm"):
    action: str
    client_id: int = 0
    object_id: int = 0
    page: int = 1


def client_list_keyboard(
    clients: list[ClientSummaryView], *, page: int, pages: int
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=client.display_name,
                callback_data=CrmCallback(action="view", client_id=client.id, page=page).pack(),
            )
        ]
        for client in clients
    ]
    navigation = []
    if page > 1:
        navigation.append(_button("◀️", "list", page=page - 1))
    if page < pages:
        navigation.append(_button("▶️", "list", page=page + 1))
    if navigation:
        rows.append(navigation)
    rows.append([_button("🔎 Поиск", "search"), _button("🏷 Все теги", "all_tags")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def client_card_keyboard(card: ClientCardView, *, page: int = 1) -> InlineKeyboardMarkup:
    block_text = (
        "✅ Разрешить самостоятельную запись"
        if card.is_self_booking_blocked
        else "⛔ Запретить самостоятельную запись"
    )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [_button("📚 История", "history", client_id=card.id)],
            [_button("🏷 Теги", "client_tags", client_id=card.id)],
            [_button("📝 Заметки", "notes", client_id=card.id)],
            [_button("✉️ Написать", "write", client_id=card.id)],
            [_button("➕ Создать запись", "manual", client_id=card.id)],
            [
                _button(
                    block_text,
                    "unblock" if card.is_self_booking_blocked else "block",
                    client_id=card.id,
                )
            ],
            [_button("🔙 К списку", "list", page=page)],
        ]
    )


def booking_limit_override_keyboard(client_id: int) -> InlineKeyboardMarkup:
    """Require a distinct confirmation before an authorized quota override."""

    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _button(
                    "Создать повторный сеанс сверх лимита",
                    "manual_override_confirm",
                    client_id=client_id,
                )
            ],
            [_button("Назад к карточке", "view", client_id=client_id)],
        ]
    )


def client_tags_keyboard(
    client_id: int,
    tags: list[ClientTagView],
    assigned_ids: set[int],
) -> InlineKeyboardMarkup:
    rows = []
    for tag in tags:
        assigned = tag.id in assigned_ids
        rows.append(
            [
                _button(
                    f"{'✅' if assigned else '➕'} {tag.marker or ''} {tag.name}".strip(),
                    "tag_remove" if assigned else "tag_assign",
                    client_id=client_id,
                    object_id=tag.id,
                )
            ]
        )
    rows.append([_button("➕ Новый тег", "tag_create", client_id=client_id)])
    rows.append([_button("🔙 К карточке", "view", client_id=client_id)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def notes_keyboard(card: ClientCardView) -> InlineKeyboardMarkup:
    rows = [
        [
            _button(
                f"🗄 Заметка {note.id}",
                "note_archive",
                client_id=card.id,
                object_id=note.id,
            )
        ]
        for note in card.notes
    ]
    rows.append([_button("➕ Добавить заметку", "note_add", client_id=card.id)])
    rows.append([_button("🔙 К карточке", "view", client_id=card.id)])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def all_tags_keyboard(tags: list[ClientTagView]) -> InlineKeyboardMarkup:
    rows = [
        [
            _button(
                f"{'✅' if tag.is_active else '🗄'} {tag.marker or ''} {tag.name}".strip(),
                "tag_archive" if tag.is_active else "tag_activate",
                object_id=tag.id,
            )
        ]
        for tag in tags
    ]
    rows.append([_button("➕ Новый тег", "tag_create")])
    rows.append([_button("🔙 Клиентки", "list")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _button(
    text: str,
    action: str,
    *,
    client_id: int = 0,
    object_id: int = 0,
    page: int = 1,
) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        callback_data=CrmCallback(
            action=action,
            client_id=client_id,
            object_id=object_id,
            page=page,
        ).pack(),
    )
