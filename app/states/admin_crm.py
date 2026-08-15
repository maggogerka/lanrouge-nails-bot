"""FSM states for administrator CRM input."""

from aiogram.fsm.state import State, StatesGroup


class AdminCrmFlow(StatesGroup):
    search = State()
    tag_create = State()
    note_create = State()
    write_client = State()
    manual_booking = State()
    manual_booking_window = State()
    manual_booking_override = State()
    manual_booking_override_reason = State()
    block_reason = State()
