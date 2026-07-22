"""Administrative broadcast builder states."""

from aiogram.fsm.state import State, StatesGroup


class BroadcastFlow(StatesGroup):
    title = State()
    text = State()
    media = State()
    audience_parameter = State()
    button_url = State()
    schedule = State()
