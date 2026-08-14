"""Master-owned availability-window creation states."""

from aiogram.fsm.state import State, StatesGroup


class MasterWindowCreate(StatesGroup):
    local_date = State()
    local_time = State()
    manual_time = State()
    duration = State()
    manual_duration = State()
    confirm = State()
