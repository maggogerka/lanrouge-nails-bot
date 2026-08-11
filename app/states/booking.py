"""Redis-backed client booking conversation states."""

from aiogram.fsm.state import State, StatesGroup

PENDING_MARKETING_BOOKING_KEY = "pending_marketing_booking"


class BookingFlow(StatesGroup):
    service = State()
    addons = State()
    master = State()
    date = State()
    window = State()
    name = State()
    phone = State()
    comment = State()
    references = State()
    confirm = State()


class BookingReferenceEdit(StatesGroup):
    uploading = State()
