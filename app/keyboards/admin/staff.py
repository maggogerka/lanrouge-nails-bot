"""Staff administration and one-time invitation controls."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.domain.enums import StaffRole
from app.schemas.authorization import StaffInvitationView, can_assign_role


class StaffAdminCallback(CallbackData, prefix="staffadm"):
    action: str
    role: str = "none"
    invitation_id: int = 0


ROLE_LABELS: dict[StaffRole, str] = {
    StaffRole.OWNER: "владелец",
    StaffRole.MANAGER: "менеджер",
    StaffRole.MASTER: "мастер",
    StaffRole.RECEPTIONIST: "администратор записи",
}


def staff_management_keyboard(
    actor_role: StaffRole,
    invitations: tuple[StaffInvitationView, ...],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for role in StaffRole:
        if can_assign_role(actor_role, role):
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"➕ Пригласить: {ROLE_LABELS[role]}",
                        callback_data=StaffAdminCallback(
                            action="invite",
                            role=role.value,
                        ).pack(),
                    )
                ]
            )
    for invitation in invitations:
        if can_assign_role(actor_role, invitation.role):
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"Отозвать #{invitation.id}: {invitation.display_name[:24]}",
                        callback_data=StaffAdminCallback(
                            action="revoke_prompt",
                            invitation_id=invitation.id,
                        ).pack(),
                    )
                ]
            )
    rows.append(
        [
            InlineKeyboardButton(
                text="Обновить",
                callback_data=StaffAdminCallback(action="list").pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def staff_invitation_link(url: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть приглашение", url=url)],
        ]
    )


def revoke_invitation_confirmation(invitation_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, отозвать",
                    callback_data=StaffAdminCallback(
                        action="revoke_confirm",
                        invitation_id=invitation_id,
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Нет",
                    callback_data=StaffAdminCallback(action="list").pack(),
                )
            ],
        ]
    )
