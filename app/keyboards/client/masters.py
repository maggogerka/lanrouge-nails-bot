"""Public master-card actions."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.schemas.public_links import PublicLink


class PublicMasterCallback(CallbackData, prefix="pmaster"):
    action: str
    staff_member_id: int


def public_master_keyboard(
    staff_member_id: int,
    social_links: tuple[PublicLink, ...] = (),
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            *[
                [InlineKeyboardButton(text=f"🔗 {link.label}", url=link.url)]
                for link in social_links
            ],
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
    )
