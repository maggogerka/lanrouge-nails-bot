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
    revoke_invitation_confirmation,
    staff_invitation_link,
    staff_management_keyboard,
)
from app.schemas.authorization import StaffContext, StaffInvitationCreate
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
        lines.append(
            f"• {escape(member.display_name)} — {ROLE_LABELS[member.role]}, {state}, {binding}"
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
        reply_markup=staff_management_keyboard(actor.role, invitations),
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
