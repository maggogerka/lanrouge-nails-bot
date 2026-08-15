"""Acquisition source administration FSM."""

from aiogram.fsm.state import State, StatesGroup


class AcquisitionStates(StatesGroup):
    waiting_code = State()
    waiting_name = State()
