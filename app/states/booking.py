"""Redis-backed client booking conversation states."""

from aiogram.fsm.state import State, StatesGroup


class BookingFlow(StatesGroup):
    service = State()
    date = State()
    window = State()
    name = State()
    phone = State()
    comment = State()
    references = State()
    confirm = State()


class BookingReferenceEdit(StatesGroup):
    uploading = State()
