"""FSM states for editing the public master profile."""

from aiogram.fsm.state import State, StatesGroup


class AdminMasterProfileEdit(StatesGroup):
    text_value = State()
    photo = State()
    link_value = State()
