"""Acquisition statistics controls."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.domain.enums import StaffRole
from app.schemas.acquisition import AcquisitionMetricsView


class AcquisitionCallback(CallbackData, prefix="acq"):
    source_id: int


def acquisition_statistics_keyboard(
    items: tuple[AcquisitionMetricsView, ...],
    *,
    actor_role: StaffRole,
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"🔗 {item.source.display_name}",
                callback_data=AcquisitionCallback(source_id=item.source.id).pack(),
            )
        ]
        for item in items
    ]
    if actor_role is StaffRole.OWNER:
        rows.append([InlineKeyboardButton(text="➕ Новый источник", callback_data="acq:new")])
    return InlineKeyboardMarkup(inline_keyboard=rows)
