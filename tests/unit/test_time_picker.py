"""Button-based time selection and manual normalization tests."""

import pytest

from app.handlers.admin.window_common import parse_local_time
from app.keyboards.common.time_picker import (
    TimePickerCallback,
    clock_values,
    decode_clock_value,
    time_picker_keyboard,
)


def test_default_picker_contains_00_to_23_without_24() -> None:
    values = clock_values()

    assert len(values) == 24
    assert values[0] == "00:00"
    assert values[-1] == "23:00"
    assert "24:00" not in values


def test_time_buttons_use_four_columns_and_have_manual_action() -> None:
    keyboard = time_picker_keyboard()

    assert all(len(row) == 4 for row in keyboard.inline_keyboard[:6])
    actions = [
        TimePickerCallback.unpack(button.callback_data or "").action
        for row in keyboard.inline_keyboard
        for button in row
    ]
    assert "manual" in actions
    assert "date" in actions
    assert "cancel" in actions
    first = TimePickerCallback.unpack(keyboard.inline_keyboard[0][0].callback_data or "")
    assert first.value == "0000"
    assert decode_clock_value(first.value) == "00:00"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("9:30", "09:30"), ("09:30", "09:30"), ("14:15", "14:15"), ("18:45", "18:45")],
)
def test_manual_time_is_normalized(raw: str, expected: str) -> None:
    parsed = parse_local_time(raw)

    assert parsed is not None
    assert parsed.strftime("%H:%M") == expected


@pytest.mark.parametrize("raw", ["24:00", "25:00", "12:70", "text", "", "930", "9:3"])
def test_invalid_manual_time_is_rejected(raw: str) -> None:
    assert parse_local_time(raw) is None


@pytest.mark.parametrize("step", [0, -1, 61, 1441])
def test_invalid_time_step_is_rejected(step: int) -> None:
    with pytest.raises(ValueError, match="divisor"):
        clock_values(step)
