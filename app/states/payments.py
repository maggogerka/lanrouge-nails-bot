"""FSM state for safe manual payment instructions."""

from aiogram.fsm.state import State, StatesGroup


class PaymentSettingsForm(StatesGroup):
    manual_instructions = State()
    manual_instructions_preview = State()
    rejection_reason = State()
    reservation_ttl = State()


class ManualReceiptUpload(StatesGroup):
    waiting = State()
