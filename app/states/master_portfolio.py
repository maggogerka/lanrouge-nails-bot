"""Self-scoped portfolio creation states for a master."""

from aiogram.fsm.state import State, StatesGroup


class MasterPortfolioCreate(StatesGroup):
    media = State()
    title = State()
    description = State()
    preview = State()
