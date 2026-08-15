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
    invitation_roles_keyboard,
    reassign_confirmation,
    revoke_invitation_confirmation,
    revoke_member_confirmation,
    staff_invitation_link,
    staff_management_keyboard,
    staff_member_keyboard,
    staff_services_keyboard,
    staff_social_links_keyboard,
)
from app.schemas.authorization import (
    StaffContext,
    StaffInvitationCreate,
    StaffPermission,
    StaffProfilePatch,
)
from app.schemas.public_links import PublicLink, public_links_from_mapping
from app.services.authorization_service import AuthorizationService
from app.states.staff import StaffInvitationForm, StaffProfileForm
from app.utils.telegram import answer_html_safely, answer_photo_with_html

router = Router(name="admin.staff")


async def _show_staff(
    message: Message,
    service: AuthorizationService,
    actor: StaffContext,
) -> None:
    members = await service.list_staff(actor)
    # Hide only obsolete unbound migration profiles. Revoked real employees
    # remain visible so their history can still be administered.
    visible_members = tuple(
        member for member in members if not (member.archived_at is not None and not member.is_bound)
    )
    invitations = await service.list_active_invitations(actor)
    lines = [
        "<b>Мастера и сотрудники</b>",
        "Роль отвечает за доступ к админке, а переключатель «Принимать записи» — "
        "за показ клиентам. Поэтому владелец может одновременно работать мастером.",
        "\nОткройте карточку человека, чтобы настроить фото, описание и услуги.",
    ]
    for member in visible_members:
        state = "активен" if member.is_active else "отключён"
        booking = " · принимает записи" if member.is_bookable else ""
        binding = "Telegram привязан" if member.is_bound else "профиль без входа"
        lines.append(
            f"• {escape(member.display_name)} — {ROLE_LABELS[member.role]}, "
            f"{state}, {binding}{booking}"
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
        reply_markup=staff_management_keyboard(actor, visible_members, invitations),
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


@router.callback_query(StaffAdminCallback.filter(F.action == "invite_menu"))
async def show_invitation_roles(
    callback: CallbackQuery,
    staff_context: StaffContext,
) -> None:
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Кого добавить? Мастер принимает записи, администратор управляет бизнесом, "
            "менеджер работает с клиентами и расписанием.",
            reply_markup=invitation_roles_keyboard(staff_context),
        )
    await callback.answer()


async def _show_member(
    message: Message,
    service: AuthorizationService,
    actor: StaffContext,
    staff_member_id: int,
) -> None:
    member = await service.get_staff_member(actor, staff_member_id)
    lines = [
        f"<b>{escape(member.display_name)}</b>",
        f"Доступ: {ROLE_LABELS[member.role]}",
        f"Запись клиентов: {'включена' if member.is_bookable else 'выключена'}",
        f"Telegram: {'привязан' if member.is_bound else 'не привязан (это допустимо для профиля)'}",
    ]
    if member.specialization:
        lines.append(f"Специализация: {escape(member.specialization)}")
    if member.bio:
        lines.append(f"О мастере: {escape(member.bio)}")
    if member.social_links:
        lines.append("Контакты: " + ", ".join(escape(label) for label in member.social_links))
    text = "\n".join(lines)
    keyboard = staff_member_keyboard(actor, member)
    if member.telegram_photo_file_id:
        await answer_photo_with_html(
            message,
            member.telegram_photo_file_id,
            text,
            reply_markup=keyboard,
        )
    else:
        await answer_html_safely(message, text, reply_markup=keyboard)


@router.callback_query(StaffAdminCallback.filter(F.action == "member"))
async def show_member(
    callback: CallbackQuery,
    callback_data: StaffAdminCallback,
    authorization_service: AuthorizationService,
    staff_context: StaffContext,
) -> None:
    try:
        if isinstance(callback.message, Message):
            await _show_member(
                callback.message,
                authorization_service,
                staff_context,
                callback_data.staff_member_id,
            )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer()


@router.callback_query(StaffAdminCallback.filter(F.action == "bookable"))
async def toggle_bookable(
    callback: CallbackQuery,
    callback_data: StaffAdminCallback,
    authorization_service: AuthorizationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    try:
        await authorization_service.set_staff_bookable(
            staff_context,
            callback_data.staff_member_id,
            enabled=callback_data.enabled,
            correlation_id=correlation_id,
        )
        if isinstance(callback.message, Message):
            await _show_member(
                callback.message,
                authorization_service,
                staff_context,
                callback_data.staff_member_id,
            )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("Настройка записи обновлена.")


@router.callback_query(StaffAdminCallback.filter(F.action == "services"))
async def show_staff_services(
    callback: CallbackQuery,
    callback_data: StaffAdminCallback,
    authorization_service: AuthorizationService,
    staff_context: StaffContext,
) -> None:
    try:
        assignments = await authorization_service.list_staff_service_assignments(
            staff_context, callback_data.staff_member_id
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "<b>Услуги мастера</b>\nНажмите на услугу, чтобы включить или выключить запись.",
            reply_markup=staff_services_keyboard(callback_data.staff_member_id, assignments),
        )
    await callback.answer()


async def _show_social_links(
    message: Message,
    service: AuthorizationService,
    actor: StaffContext,
    staff_member_id: int,
) -> None:
    member = await service.get_staff_member(actor, staff_member_id)
    links = public_links_from_mapping(member.social_links)
    lines = [
        f"<b>Контакты: {escape(member.display_name)}</b>",
        "До 5 HTTPS-ссылок. Первая ссылка используется кнопкой «Написать мастеру» "
        "в подтверждении записи.",
    ]
    lines.extend(f"• {escape(link.label)} — {escape(link.url)}" for link in links)
    if not links:
        lines.append("\nКонтакты ещё не добавлены.")
    await message.answer(
        "\n".join(lines),
        reply_markup=staff_social_links_keyboard(staff_member_id, links),
    )


@router.callback_query(StaffAdminCallback.filter(F.action == "socials"))
async def show_social_links(
    callback: CallbackQuery,
    callback_data: StaffAdminCallback,
    authorization_service: AuthorizationService,
    staff_context: StaffContext,
) -> None:
    try:
        if isinstance(callback.message, Message):
            await _show_social_links(
                callback.message,
                authorization_service,
                staff_context,
                callback_data.staff_member_id,
            )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer()


@router.callback_query(StaffAdminCallback.filter(F.action == "social_add"))
async def begin_social_link(
    callback: CallbackQuery,
    callback_data: StaffAdminCallback,
    state: FSMContext,
    authorization_service: AuthorizationService,
    staff_context: StaffContext,
) -> None:
    member = await authorization_service.get_staff_member(
        staff_context, callback_data.staff_member_id
    )
    if len(member.social_links) >= 5:
        await callback.answer("Можно добавить не более 5 контактов.", show_alert=True)
        return
    await state.set_state(StaffProfileForm.value)
    await state.set_data({"staff_member_id": member.id, "profile_field": "social_label"})
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Введите название кнопки, например «Telegram», «WhatsApp» или «VK»:",
            reply_markup=cancel_keyboard(),
        )
    await callback.answer()


@router.callback_query(StaffAdminCallback.filter(F.action == "social_delete"))
async def delete_social_link(
    callback: CallbackQuery,
    callback_data: StaffAdminCallback,
    authorization_service: AuthorizationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    member = await authorization_service.get_staff_member(
        staff_context, callback_data.staff_member_id
    )
    links = list(public_links_from_mapping(member.social_links))
    index = callback_data.target_staff_member_id
    if index < 0 or index >= len(links):
        await callback.answer("Список уже изменился. Откройте его заново.", show_alert=True)
        return
    del links[index]
    await authorization_service.set_staff_social_links(
        staff_context,
        member.id,
        {link.label: link.url for link in links},
        correlation_id=correlation_id,
    )
    if isinstance(callback.message, Message):
        await _show_social_links(callback.message, authorization_service, staff_context, member.id)
    await callback.answer("Контакт удалён.")


@router.callback_query(StaffAdminCallback.filter(F.action == "service_toggle"))
async def toggle_staff_service(
    callback: CallbackQuery,
    callback_data: StaffAdminCallback,
    authorization_service: AuthorizationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    try:
        await authorization_service.set_staff_service_assignment(
            staff_context,
            callback_data.staff_member_id,
            callback_data.target_staff_member_id,
            enabled=callback_data.enabled,
            correlation_id=correlation_id,
        )
        assignments = await authorization_service.list_staff_service_assignments(
            staff_context, callback_data.staff_member_id
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(
            reply_markup=staff_services_keyboard(callback_data.staff_member_id, assignments)
        )
    await callback.answer("Услуги обновлены.")


_PROFILE_ACTIONS = {
    "edit_name": ("display_name", "Введите имя, которое увидят клиенты:"),
    "edit_specialization": ("specialization", "Введите специализацию мастера:"),
    "edit_bio": ("bio", "Расскажите о мастере (до 4000 символов):"),
    "edit_photo": ("photo", "Отправьте одну фотографию мастера:"),
}


@router.callback_query(StaffAdminCallback.filter(F.action.in_(_PROFILE_ACTIONS)))
async def begin_profile_edit(
    callback: CallbackQuery,
    callback_data: StaffAdminCallback,
    state: FSMContext,
) -> None:
    field, prompt = _PROFILE_ACTIONS[callback_data.action]
    await state.set_state(StaffProfileForm.value)
    await state.set_data({"staff_member_id": callback_data.staff_member_id, "profile_field": field})
    if isinstance(callback.message, Message):
        await callback.message.answer(prompt, reply_markup=cancel_keyboard())
    await callback.answer()


@router.message(StaffProfileForm.value)
async def save_profile_edit(
    message: Message,
    state: FSMContext,
    authorization_service: AuthorizationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    data = await state.get_data()
    try:
        staff_member_id = int(str(data["staff_member_id"]))
        field = str(data["profile_field"])
        if field == "social_label":
            label = (message.text or "").strip()
            PublicLink(label=label, url="https://example.test")
            await state.update_data(profile_field="social_url", social_label=label)
            await message.answer(
                "Теперь пришлите HTTPS-ссылку для этой кнопки:",
                reply_markup=cancel_keyboard(),
            )
            return
        if field == "social_url":
            new_link = PublicLink(
                label=str(data.get("social_label", "")),
                url=message.text or "",
            )
            member = await authorization_service.get_staff_member(staff_context, staff_member_id)
            links = dict(member.social_links)
            links[new_link.label] = new_link.url
            await authorization_service.set_staff_social_links(
                staff_context,
                staff_member_id,
                links,
                correlation_id=correlation_id,
            )
        elif field == "photo":
            if not message.photo:
                await message.answer("Нужно отправить фотографию, не файл и не текст.")
                return
            photo = message.photo[-1]
            patch = StaffProfilePatch(
                telegram_photo_file_id=photo.file_id,
                telegram_photo_file_unique_id=photo.file_unique_id,
            )
        else:
            patch = StaffProfilePatch(**{field: message.text or ""})
        if field not in {"social_label", "social_url"}:
            await authorization_service.update_staff_profile(
                staff_context,
                staff_member_id,
                patch,
                correlation_id=correlation_id,
            )
    except (DomainError, ValidationError, KeyError, ValueError) as exc:
        await message.answer(f"Не удалось сохранить профиль: {exc}")
        return
    await state.clear()
    await message.answer("Профиль мастера обновлён.")
    await _show_member(message, authorization_service, staff_context, staff_member_id)


@router.callback_query(StaffAdminCallback.filter(F.action == "clear_photo"))
async def clear_profile_photo(
    callback: CallbackQuery,
    callback_data: StaffAdminCallback,
    authorization_service: AuthorizationService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    try:
        await authorization_service.update_staff_profile(
            staff_context,
            callback_data.staff_member_id,
            StaffProfilePatch(
                telegram_photo_file_id=None,
                telegram_photo_file_unique_id=None,
            ),
            correlation_id=correlation_id,
        )
    except (DomainError, ValidationError) as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("Фото удалено.")
    if isinstance(callback.message, Message):
        await _show_member(
            callback.message,
            authorization_service,
            staff_context,
            callback_data.staff_member_id,
        )


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
