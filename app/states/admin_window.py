"""Administrative availability-window FSM states."""

from aiogram.fsm.state import State, StatesGroup


class AdminWindowCreate(StatesGroup):
    """Button-first window draft with explicit confirmation."""

    master = State()
    local_date = State()
    local_time = State()
    manual_time = State()
    duration = State()
    comment = State()
    confirm = State()
    completed = State()
