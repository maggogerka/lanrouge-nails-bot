"""FSM states for issuing one-time staff invitations."""

from aiogram.fsm.state import State, StatesGroup


class StaffInvitationForm(StatesGroup):
    display_name = State()
