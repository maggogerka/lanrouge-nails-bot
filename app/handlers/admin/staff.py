"""Staff list and secure one-time invitation workflow."""

from __future__ import annotations

from html import escape

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from pydantic import ValidationError

from app.domain.enums import StaffRole
from app.domain.errors import DomainError
from app.keyboards.admin.main import ADMIN_STAFF_TEXT
from app.keyboards.admin.services import cancel_keyboard
from app.keyboards.admin.staff import (
    ROLE_LABELS,
    StaffAdminCallback,
    reassign_confirmation,
    revoke_invitation_confirmation,
    revoke_member_confirmation,
    staff_invitation_link,
    staff_management_keyboard,
)
from app.schemas.authorization import StaffContext, StaffInvitationCreate, StaffPermission
from app.services.authorization_service import AuthorizationService
from app.states.staff import StaffInvitationForm

router = Router(name="admin.staff")


async def _show_staff(
    message: Message,
    service: AuthorizationService,
    actor: StaffContext,
) -> None:
    members = await service.list_staff(actor)
    invitations = await service.list_active_invitations(actor)
    lines = ["<b>Мастера и сотрудники</b>"]
    for member in members:
        state = "активен" if member.is_active else "отключён"
        binding = "Telegram привязан" if member.is_bound else "без Telegram"
        bootstrap = " · bootstrap-владелец" if member.is_bootstrap_owner else ""
        lines.append(
            f"• {escape(member.display_name)} — {ROLE_LABELS[member.role]}, "
            f"{state}, {binding}{bootstrap}"
        )
    if invitations:
        lines.append("\n<b>Ожидают принятия</b>")
        lines.extend(
            f"• #{item.id} {escape(item.display_name)} — {ROLE_LABELS[item.role]}, "
            f"до {item.expires_at:%d.%m.%Y %H:%M UTC}"
            for item in invitations
        )
    await message.answer(
        "\n".join(lines),
        reply_markup=staff_management_keyboard(actor, members, invitations),
    )


@router.message(F.text == ADMIN_STAFF_TEXT)
@router.message(Command("staff"))
async def show_staff(
    message: Message,
    authorization_service: AuthorizationService,
    staff_context: StaffContext,
) -> None:
    try:
        await _show_staff(message, authorization_service, staff_context)
    except DomainError as exc:
        await message.answer(str(exc))


@router.callback_query(StaffAdminCallback.filter(F.action == "list"))
async def refresh_staff(
    callback: CallbackQuery,
    authorization_service: AuthorizationService,
    staff_context: StaffContext,
) -> None:
    if isinstance(callback.message, Message):
        try:
            await _show_staff(callback.message, authorization_service, staff_context)
        except DomainError as exc:
            await callback.answer(str(exc), show_alert=True)
            return
    await callback.answer()


@router.callback_query(StaffAdminCallback.filter(F.action == "invite"))
async def begin_staff_invitation(
    callback: CallbackQuery,
    callback_data: StaffAdminCallback,
    state: FSMContext,
) -> None:
    try:
        role = StaffRole(callback_data.role)
    except ValueError:
        await callback.answer("Некорректная роль.", show_alert=True)
        return
    await state.set_state(StaffInvitationForm.display_name)
    await state.set_data({"staff_invitation_role": role.value})
    if isinstance(callback.message, Message):
        await callback.message.answer(
            f"Введите отображаемое имя для роли «{ROLE_LABELS[role]}».\n"
            "Ссылка будет одноразовой и действительна 24 часа.",
            reply_markup=cancel_keyboard(),
        )
    await callback.answer()


@router.message(StaffInvitationForm.display_name)
async def issue_staff_invitation(
    message: Message,
    state: FSMContext,
    bot: Bot,
    authorization_service: AuthorizationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    data = await state.get_data()
    try:
        role = StaffRole(str(data.get("staff_invitation_role", "")))
        values = StaffInvitationCreate(
            role=role,
            display_name=message.text or "",
            is_bookable=role is StaffRole.MASTER,
            expires_in_hours=24,
        )
        issued = await authorization_service.issue_invitation(
            staff_context,
            values,
            correlation_id=correlation_id,
        )
        bot_user = await bot.get_me()
        if not bot_user.username:
            raise RuntimeError("bot username is unavailable")
    except (DomainError, ValidationError, ValueError) as exc:
        await message.answer(f"Не удалось создать приглашение: {exc}")
        return
    except RuntimeError:
        await message.answer(
            "Приглашение создано, но Telegram-ссылка недоступна. "
            "Проверьте username бота в BotFather и создайте новое приглашение."
        )
        await state.clear()
        return

    token = issued.token.get_secret_value()
    url = f"https://t.me/{bot_user.username}?start=staff_{token}"
    await state.clear()
    await message.answer(
        f"Одноразовое приглашение для <b>{escape(issued.display_name)}</b> создано.\n"
        f"Действует до {issued.expires_at:%d.%m.%Y %H:%M UTC}. "
        "После первого принятия ссылка перестанет работать.",
        reply_markup=staff_invitation_link(url),
    )


@router.callback_query(StaffAdminCallback.filter(F.action == "revoke_prompt"))
async def prompt_revoke_invitation(
    callback: CallbackQuery,
    callback_data: StaffAdminCallback,
) -> None:
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Отозвать приглашение? Эту ссылку больше нельзя будет использовать.",
            reply_markup=revoke_invitation_confirmation(callback_data.invitation_id),
        )
    await callback.answer()


@router.callback_query(StaffAdminCallback.filter(F.action == "revoke_confirm"))
async def revoke_invitation(
    callback: CallbackQuery,
    callback_data: StaffAdminCallback,
    authorization_service: AuthorizationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    try:
        await authorization_service.revoke_invitation(
            staff_context,
            callback_data.invitation_id,
            correlation_id=correlation_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("Приглашение отозвано.")
    if isinstance(callback.message, Message):
        await _show_staff(callback.message, authorization_service, staff_context)


@router.callback_query(StaffAdminCallback.filter(F.action == "member_revoke_prompt"))
async def prompt_revoke_member(
    callback: CallbackQuery,
    callback_data: StaffAdminCallback,
) -> None:
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Отозвать роль сотрудника? Доступ прекратится сразу, история сохранится.",
            reply_markup=revoke_member_confirmation(callback_data.staff_member_id),
        )
    await callback.answer()


@router.callback_query(StaffAdminCallback.filter(F.action == "member_revoke_confirm"))
async def revoke_member(
    callback: CallbackQuery,
    callback_data: StaffAdminCallback,
    authorization_service: AuthorizationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    try:
        await authorization_service.revoke_member(
            staff_context,
            callback_data.staff_member_id,
            correlation_id=correlation_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("Роль отозвана.")
    if isinstance(callback.message, Message):
        await _show_staff(callback.message, authorization_service, staff_context)


@router.callback_query(StaffAdminCallback.filter(F.action == "reassign_prompt"))
async def prompt_reassign_future_appointments(
    callback: CallbackQuery,
    callback_data: StaffAdminCallback,
) -> None:
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Переназначить все будущие активные записи? Для нового специалиста должны "
            "быть назначены те же услуги и созданы свободные окна на то же время.",
            reply_markup=reassign_confirmation(
                callback_data.staff_member_id,
                callback_data.target_staff_member_id,
            ),
        )
    await callback.answer()


@router.callback_query(StaffAdminCallback.filter(F.action == "reassign_confirm"))
async def reassign_future_appointments(
    callback: CallbackQuery,
    callback_data: StaffAdminCallback,
    authorization_service: AuthorizationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    try:
        count = await authorization_service.reassign_future_appointments(
            staff_context,
            callback_data.staff_member_id,
            callback_data.target_staff_member_id,
            correlation_id=correlation_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer(f"Переназначено записей: {count}.", show_alert=True)
    if isinstance(callback.message, Message):
        await _show_staff(callback.message, authorization_service, staff_context)


@router.callback_query(StaffAdminCallback.filter(F.action == "role"))
async def change_member_role(
    callback: CallbackQuery,
    callback_data: StaffAdminCallback,
    authorization_service: AuthorizationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    try:
        role = StaffRole(callback_data.role)
        await authorization_service.change_member_role(
            staff_context,
            callback_data.staff_member_id,
            role,
            correlation_id=correlation_id,
        )
    except (DomainError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("Роль обновлена.")
    if isinstance(callback.message, Message):
        await _show_staff(callback.message, authorization_service, staff_context)


@router.callback_query(StaffAdminCallback.filter(F.action == "perm"))
async def toggle_member_permission(
    callback: CallbackQuery,
    callback_data: StaffAdminCallback,
    authorization_service: AuthorizationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    try:
        permission = StaffPermission(callback_data.permission)
        await authorization_service.set_permission_grant(
            staff_context,
            callback_data.staff_member_id,
            permission,
            enabled=callback_data.enabled,
            correlation_id=correlation_id,
        )
    except (DomainError, ValueError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("Разрешение обновлено.")
    if isinstance(callback.message, Message):
        await _show_staff(callback.message, authorization_service, staff_context)
