"""Single-value administrator settings editor state."""

from aiogram.fsm.state import State, StatesGroup


class AdminSettingsEdit(StatesGroup):
    value = State()
