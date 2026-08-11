"""Staff administration and one-time invitation controls."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.domain.enums import StaffRole
from app.schemas.authorization import (
    StaffContext,
    StaffInvitationView,
    StaffMemberView,
    StaffPermission,
    can_assign_role,
)


class StaffAdminCallback(CallbackData, prefix="staffadm"):
    action: str
    role: str = "none"
    invitation_id: int = 0
    staff_member_id: int = 0
    target_staff_member_id: int = 0
    permission: str = "none"
    enabled: bool = False


ROLE_LABELS: dict[StaffRole, str] = {
    StaffRole.OWNER: "владелец",
    StaffRole.MANAGER: "администратор",
    StaffRole.MASTER: "мастер",
    StaffRole.RECEPTIONIST: "менеджер",
}


def staff_management_keyboard(
    actor: StaffContext,
    members: tuple[StaffMemberView, ...],
    invitations: tuple[StaffInvitationView, ...],
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if actor.has_permission(StaffPermission.INVITE_STAFF):
        for role in StaffRole:
            if not can_assign_role(
                actor.role,
                role,
                actor_is_bootstrap=actor.is_bootstrap_owner,
            ):
                continue
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
        if can_assign_role(
            actor.role,
            invitation.role,
            actor_is_bootstrap=actor.is_bootstrap_owner,
        ):
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
    if actor.has_permission(StaffPermission.MANAGE_STAFF):
        for member in members:
            owner_allowed = member.role is not StaffRole.OWNER or actor.is_bootstrap_owner
            if (
                member.is_active
                and not member.is_bootstrap_owner
                and member.id != actor.staff_member_id
                and owner_allowed
            ):
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=f"Отозвать роль: {member.display_name[:24]}",
                            callback_data=StaffAdminCallback(
                                action="member_revoke_prompt",
                                staff_member_id=member.id,
                            ).pack(),
                        )
                    ]
                )
                for role in StaffRole:
                    if role is member.role or not can_assign_role(
                        actor.role,
                        role,
                        actor_is_bootstrap=actor.is_bootstrap_owner,
                    ):
                        continue
                    rows.append(
                        [
                            InlineKeyboardButton(
                                text=(f"Роль {member.display_name[:12]} → {ROLE_LABELS[role]}"),
                                callback_data=StaffAdminCallback(
                                    action="role",
                                    role=role.value,
                                    staff_member_id=member.id,
                                ).pack(),
                            )
                        ]
                    )
        targets = [member for member in members if member.is_active and member.is_bookable]
        sources = [
            member
            for member in members
            if not member.is_active
            and not member.is_bootstrap_owner
            and (member.role is not StaffRole.OWNER or actor.is_bootstrap_owner)
        ]
        for source in sources:
            for target in targets:
                if source.id == target.id:
                    continue
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=(
                                f"Переназначить: {source.display_name[:12]} → "
                                f"{target.display_name[:12]}"
                            ),
                            callback_data=StaffAdminCallback(
                                action="reassign_prompt",
                                staff_member_id=source.id,
                                target_staff_member_id=target.id,
                            ).pack(),
                        )
                    ]
                )
    if actor.role is StaffRole.OWNER:
        permission_labels = {
            StaffPermission.INVITE_STAFF: "приглашения",
            StaffPermission.MANAGE_STAFF: "управление штатом",
            StaffPermission.MANAGE_BROADCASTS: "рассылки",
            StaffPermission.OVERRIDE_BOOKING_LIMIT: "превышение лимита",
        }
        for member in members:
            if not member.is_active or member.role is StaffRole.OWNER:
                continue
            for permission, label in permission_labels.items():
                enabled = permission not in member.permission_grants
                rows.append(
                    [
                        InlineKeyboardButton(
                            text=(
                                f"{'Выдать' if enabled else 'Отозвать'} {label}: "
                                f"{member.display_name[:12]}"
                            ),
                            callback_data=StaffAdminCallback(
                                action="perm",
                                staff_member_id=member.id,
                                permission=permission.value,
                                enabled=enabled,
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


def revoke_member_confirmation(staff_member_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, отозвать роль",
                    callback_data=StaffAdminCallback(
                        action="member_revoke_confirm",
                        staff_member_id=staff_member_id,
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


def reassign_confirmation(
    source_staff_member_id: int,
    target_staff_member_id: int,
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Да, переназначить будущие записи",
                    callback_data=StaffAdminCallback(
                        action="reassign_confirm",
                        staff_member_id=source_staff_member_id,
                        target_staff_member_id=target_staff_member_id,
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
