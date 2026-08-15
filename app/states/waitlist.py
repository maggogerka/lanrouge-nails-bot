"""FSM states for waitlist preferences and administrative actions."""

from aiogram.fsm.state import State, StatesGroup


class WaitlistFlow(StatesGroup):
    date_from = State()
    date_to = State()
    time_range = State()


class AdminWaitlistFlow(StatesGroup):
    message = State()
    offer_window = State()
