"""FSM states for creating a portfolio work inside Telegram."""

from aiogram.fsm.state import State, StatesGroup


class AdminPortfolioCreate(StatesGroup):
    master = State()
    media = State()
    title = State()
    description = State()
    linked_service = State()
    design_price = State()
    sort_order = State()
    tags = State()
    preview = State()


class AdminPortfolioSettings(StatesGroup):
    external_url = State()
    button_text = State()
