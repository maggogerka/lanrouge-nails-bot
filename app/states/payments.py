"""FSM state for safe manual payment instructions."""

from aiogram.fsm.state import State, StatesGroup


class PaymentSettingsForm(StatesGroup):
    manual_instructions = State()
