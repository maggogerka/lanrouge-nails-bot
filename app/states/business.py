"""White-label business setup FSM."""

from aiogram.fsm.state import State, StatesGroup


class BusinessProfileStates(StatesGroup):
    waiting_value = State()
    waiting_logo = State()
    waiting_support_label = State()
    waiting_support_url = State()


class BusinessWelcomeStates(StatesGroup):
    waiting_text = State()
    waiting_photo = State()


class BusinessWorkstationStates(StatesGroup):
    waiting_name = State()
