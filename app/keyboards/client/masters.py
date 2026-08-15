"""Public master-card actions."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.schemas.public_links import PublicLink


class PublicMasterCallback(CallbackData, prefix="pmaster"):
    action: str
    staff_member_id: int
    page: int = 1


def public_master_keyboard(
    staff_member_id: int,
    social_links: tuple[PublicLink, ...] = (),
    *,
    page: int = 1,
    pages: int = 1,
    has_photo: bool = False,
) -> InlineKeyboardMarkup:
    rows = [
        *[[InlineKeyboardButton(text=f"🔗 {link.label}", url=link.url)] for link in social_links],
        [
            InlineKeyboardButton(
                text="✨ Записаться",
                callback_data=PublicMasterCallback(
                    action="book",
                    staff_member_id=staff_member_id,
                ).pack(),
            )
        ],
    ]
    if has_photo:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🖼 Посмотреть фотографию",
                    callback_data=PublicMasterCallback(
                        action="photo", staff_member_id=staff_member_id, page=page
                    ).pack(),
                )
            ]
        )
    if pages > 1:
        navigation: list[InlineKeyboardButton] = []
        if page > 1:
            navigation.append(
                InlineKeyboardButton(
                    text="◀️",
                    callback_data=PublicMasterCallback(
                        action="page", staff_member_id=0, page=page - 1
                    ).pack(),
                )
            )
        navigation.append(
            InlineKeyboardButton(
                text=f"{page}/{pages}",
                callback_data=PublicMasterCallback(
                    action="page", staff_member_id=0, page=page
                ).pack(),
            )
        )
        if page < pages:
            navigation.append(
                InlineKeyboardButton(
                    text="▶️",
                    callback_data=PublicMasterCallback(
                        action="page", staff_member_id=0, page=page + 1
                    ).pack(),
                )
            )
        rows.append(navigation)
    return InlineKeyboardMarkup(inline_keyboard=rows)
