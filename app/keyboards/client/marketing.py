"""Tracked internal broadcast actions."""

from aiogram.filters.callback_data import CallbackData


class MarketingCallback(CallbackData, prefix="mkt"):
    action: str
    broadcast_id: int
