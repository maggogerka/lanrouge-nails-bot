"""Role/profile separation in the staff administration UI."""

from app.domain.enums import StaffRole
from app.keyboards.admin.staff import staff_management_keyboard, staff_member_keyboard
from app.schemas.authorization import StaffContext, StaffMemberView


def owner_context() -> StaffContext:
    return StaffContext(
        business_id=1,
        staff_member_id=2,
        user_id=2,
        telegram_id=22,
        display_name="Владелец",
        role=StaffRole.OWNER,
        is_bookable=False,
        is_bootstrap_owner=True,
    )


def owner_member(*, bookable: bool) -> StaffMemberView:
    return StaffMemberView(
        id=2,
        display_name="Владелец",
        role=StaffRole.OWNER,
        is_active=True,
        is_bookable=bookable,
        is_bootstrap_owner=True,
        is_bound=True,
    )


def labels(keyboard: object) -> list[str]:
    return [
        button.text
        for row in keyboard.inline_keyboard  # type: ignore[attr-defined]
        for button in row
    ]


def test_staff_root_is_compact_and_opens_member_cards() -> None:
    member = owner_member(bookable=False)
    visible = labels(staff_management_keyboard(owner_context(), (member,), ()))

    assert any("Владелец" in label for label in visible)
    assert "➕ Добавить сотрудника" in visible
    assert not any("Роль Владелец" in label for label in visible)


def test_owner_can_enable_booking_without_losing_owner_role() -> None:
    visible = labels(staff_member_keyboard(owner_context(), owner_member(bookable=False)))

    assert "✅ Принимать записи" in visible
    assert "🛠 Назначить услуги" in visible
    assert "📸 Фото" in visible
