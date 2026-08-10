"""White-label business setup FSM."""

from aiogram.fsm.state import State, StatesGroup


class BusinessProfileStates(StatesGroup):
    waiting_value = State()
    waiting_logo = State()
