"""Client review form state."""

from aiogram.fsm.state import State, StatesGroup


class ReviewFlow(StatesGroup):
    rating = State()
    text = State()
    publication = State()
    confirmation = State()
