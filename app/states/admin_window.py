"""Administrative availability-window FSM states."""

from aiogram.fsm.state import State, StatesGroup


class AdminWindowCreate(StatesGroup):
    """Sequential local date/time window input."""

    local_date = State()
    local_time = State()
    duration = State()
    comment = State()
    status = State()
