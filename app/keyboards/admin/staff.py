"""Staff administration and one-time invitation controls."""

from aiogram.filters.callback_data import CallbackData
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.domain.enums import StaffRole
from app.schemas.authorization import (
    StaffContext,
    StaffInvitationView,
    StaffMemberView,
    StaffPermission,
    StaffServiceAssignmentView,
    can_assign_role,
)
from app.schemas.public_links import PublicLink


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
    for member in members:
        icon = "👑" if member.role is StaffRole.OWNER else "💅" if member.is_bookable else "👤"
        status = "" if member.is_active else " · отключён"
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{icon} {member.display_name[:28]}{status}",
                    callback_data=StaffAdminCallback(
                        action="member",
                        staff_member_id=member.id,
                    ).pack(),
                )
            ]
        )
    if actor.has_permission(StaffPermission.INVITE_STAFF):
        rows.append(
            [
                InlineKeyboardButton(
                    text="➕ Добавить сотрудника",
                    callback_data=StaffAdminCallback(action="invite_menu").pack(),
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
    rows.append(
        [
            InlineKeyboardButton(
                text="Обновить",
                callback_data=StaffAdminCallback(action="list").pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def invitation_roles_keyboard(actor: StaffContext) -> InlineKeyboardMarkup:
    rows = []
    for role in StaffRole:
        if can_assign_role(actor.role, role, actor_is_bootstrap=actor.is_bootstrap_owner):
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"➕ {ROLE_LABELS[role].capitalize()}",
                        callback_data=StaffAdminCallback(action="invite", role=role.value).pack(),
                    )
                ]
            )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Назад", callback_data=StaffAdminCallback(action="list").pack()
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def staff_member_keyboard(actor: StaffContext, member: StaffMemberView) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if actor.has_permission(StaffPermission.MANAGE_STAFF) and member.is_active:
        if member.role in {StaffRole.OWNER, StaffRole.MASTER}:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=(
                            "⏸ Не принимать записи" if member.is_bookable else "✅ Принимать записи"
                        ),
                        callback_data=StaffAdminCallback(
                            action="bookable",
                            staff_member_id=member.id,
                            enabled=not member.is_bookable,
                        ).pack(),
                    )
                ]
            )
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text="🛠 Назначить услуги",
                        callback_data=StaffAdminCallback(
                            action="services", staff_member_id=member.id
                        ).pack(),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="✏️ Имя",
                        callback_data=StaffAdminCallback(
                            action="edit_name", staff_member_id=member.id
                        ).pack(),
                    ),
                    InlineKeyboardButton(
                        text="💅 Специализация",
                        callback_data=StaffAdminCallback(
                            action="edit_specialization", staff_member_id=member.id
                        ).pack(),
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="📝 О мастере",
                        callback_data=StaffAdminCallback(
                            action="edit_bio", staff_member_id=member.id
                        ).pack(),
                    ),
                    InlineKeyboardButton(
                        text="📸 Фото",
                        callback_data=StaffAdminCallback(
                            action="edit_photo", staff_member_id=member.id
                        ).pack(),
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="🔗 Контакты и соцсети",
                        callback_data=StaffAdminCallback(
                            action="socials", staff_member_id=member.id
                        ).pack(),
                    )
                ],
            ]
        )
        if member.telegram_photo_file_id:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="🗑 Удалить фото",
                        callback_data=StaffAdminCallback(
                            action="clear_photo", staff_member_id=member.id
                        ).pack(),
                    )
                ]
            )
        if (
            not member.is_bootstrap_owner
            and member.id != actor.staff_member_id
            and (member.role is not StaffRole.OWNER or actor.is_bootstrap_owner)
        ):
            rows.append(
                [
                    InlineKeyboardButton(
                        text="🚫 Отозвать доступ",
                        callback_data=StaffAdminCallback(
                            action="member_revoke_prompt", staff_member_id=member.id
                        ).pack(),
                    )
                ]
            )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ К сотрудникам", callback_data=StaffAdminCallback(action="list").pack()
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def staff_social_links_keyboard(
    staff_member_id: int,
    links: tuple[PublicLink, ...],
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"🗑 {link.label[:45]}",
                callback_data=StaffAdminCallback(
                    action="social_delete",
                    staff_member_id=staff_member_id,
                    target_staff_member_id=index,
                ).pack(),
            )
        ]
        for index, link in enumerate(links)
    ]
    if len(links) < 5:
        rows.append(
            [
                InlineKeyboardButton(
                    text="➕ Добавить контакт",
                    callback_data=StaffAdminCallback(
                        action="social_add", staff_member_id=staff_member_id
                    ).pack(),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ К профилю",
                callback_data=StaffAdminCallback(
                    action="member", staff_member_id=staff_member_id
                ).pack(),
            )
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def staff_services_keyboard(
    staff_member_id: int,
    assignments: tuple[StaffServiceAssignmentView, ...],
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'✅' if item.assigned else '⬜'} {item.service_name[:35]}",
                callback_data=StaffAdminCallback(
                    action="service_toggle",
                    staff_member_id=staff_member_id,
                    target_staff_member_id=item.service_id,
                    enabled=not item.assigned,
                ).pack(),
            )
        ]
        for item in assignments
    ]
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ К профилю",
                callback_data=StaffAdminCallback(
                    action="member", staff_member_id=staff_member_id
                ).pack(),
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
