"""Redis-backed administrative service catalog FSM states."""

from aiogram.fsm.state import State, StatesGroup


class AdminServiceCreate(StatesGroup):
    """Sequential service creation fields."""

    name = State()
    description = State()
    price = State()
    duration_min = State()
    duration_max = State()
    prepayment = State()


class AdminServiceEdit(StatesGroup):
    """One-step edits of an existing service."""

    name = State()
    description = State()
    price = State()
    duration = State()
    prepayment = State()
    photo = State()


class AdminAddonCreate(StatesGroup):
    name = State()
    description = State()
    price = State()
    duration = State()
    photo = State()


class AdminAddonEdit(StatesGroup):
    name = State()
    description = State()
    price = State()
    duration = State()
    photo = State()
