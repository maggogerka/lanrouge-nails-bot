"""Master panel remains isolated from legacy administrator controls."""

from __future__ import annotations

from app.domain.enums import StaffRole
from app.keyboards.admin.main import (
    ADMIN_CLIENTS_TEXT,
    ADMIN_SERVICES_TEXT,
    ADMIN_SETTINGS_TEXT,
)
from app.keyboards.master.main import (
    MASTER_APPOINTMENTS_TEXT,
    MASTER_SCHEDULE_TEXT,
    MASTER_SUPPORT_TEXT,
    master_main_keyboard,
)
from app.schemas.authorization import StaffContext


def master_context() -> StaffContext:
    return StaffContext(
        business_id=1,
        staff_member_id=7,
        user_id=9,
        telegram_id=123,
        display_name="Мастер",
        role=StaffRole.MASTER,
        is_bookable=True,
    )


def test_master_keyboard_contains_only_self_scoped_sections() -> None:
    keyboard = master_main_keyboard(master_context())
    labels = {button.text for row in keyboard.keyboard for button in row}

    assert labels == {
        MASTER_APPOINTMENTS_TEXT,
        MASTER_SCHEDULE_TEXT,
        MASTER_SUPPORT_TEXT,
    }
    assert labels.isdisjoint({ADMIN_CLIENTS_TEXT, ADMIN_SERVICES_TEXT, ADMIN_SETTINGS_TEXT})


def test_master_keyboard_fails_closed_without_verified_context() -> None:
    assert not master_main_keyboard().keyboard
