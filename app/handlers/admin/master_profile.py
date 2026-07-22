"""Administrative editing and preview of the public master profile."""

from __future__ import annotations

from html import escape

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from pydantic import ValidationError

from app.domain.errors import DomainError
from app.handlers.admin.service_common import actor_from_telegram
from app.keyboards.admin.main import ADMIN_MASTER_PROFILE_TEXT
from app.keyboards.admin.master_profile import (
    MasterProfileAdminCallback,
    master_links_keyboard,
    master_profile_keyboard,
)
from app.keyboards.admin.services import cancel_keyboard
from app.keyboards.client.master_profile import master_profile_links_keyboard
from app.schemas.master_profile import MasterProfileUpdate, MasterProfileView, MasterPublicLinkInput
from app.services.master_profile_service import MasterProfileService
from app.states.admin_master_profile import AdminMasterProfileEdit

router = Router(name="admin.master_profile")


@router.message(F.text == ADMIN_MASTER_PROFILE_TEXT)
async def show_master_profile_menu(
    message: Message, master_profile_service: MasterProfileService
) -> None:
    if message.from_user is None:
        return
    profile = await master_profile_service.get_admin(actor_from_telegram(message.from_user))
    await message.answer(_render_admin(profile), reply_markup=master_profile_keyboard(profile))


@router.callback_query(MasterProfileAdminCallback.filter(F.action == "menu"))
async def show_master_profile_menu_callback(
    callback: CallbackQuery, master_profile_service: MasterProfileService
) -> None:
    profile = await master_profile_service.get_admin(actor_from_telegram(callback.from_user))
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _render_admin(profile), reply_markup=master_profile_keyboard(profile)
        )
    await callback.answer()


@router.callback_query(
    MasterProfileAdminCallback.filter(
        F.action.in_({"edit_name", "edit_bio", "edit_address", "edit_map", "edit_telegram"})
    )
)
async def begin_text_edit(
    callback: CallbackQuery,
    callback_data: MasterProfileAdminCallback,
    state: FSMContext,
) -> None:
    prompts = {
        "edit_name": "Введите отображаемое имя мастера:",
        "edit_bio": "Введите описание до 4000 символов или «-», чтобы очистить:",
        "edit_address": "Введите адрес или «-», чтобы очистить:",
        "edit_map": "Введите абсолютный HTTPS URL карты или «-», чтобы очистить:",
        "edit_telegram": "Введите абсолютный HTTPS URL Telegram или «-», чтобы очистить:",
    }
    await state.set_state(AdminMasterProfileEdit.text_value)
    await state.update_data(profile_field=callback_data.action)
    if isinstance(callback.message, Message):
        await callback.message.answer(prompts[callback_data.action], reply_markup=cancel_keyboard())
    await callback.answer()


@router.message(AdminMasterProfileEdit.text_value)
async def save_text_edit(
    message: Message,
    state: FSMContext,
    master_profile_service: MasterProfileService,
    correlation_id: str,
) -> None:
    if message.from_user is None:
        return
    action = str((await state.get_data()).get("profile_field", ""))
    fields = {
        "edit_name": "display_name",
        "edit_bio": "bio",
        "edit_address": "address",
        "edit_map": "map_url",
        "edit_telegram": "telegram_url",
    }
    field = fields.get(action)
    if field is None:
        await state.clear()
        return
    raw_value = (message.text or "").strip()
    value = None if raw_value == "-" else raw_value
    try:
        profile = await master_profile_service.update(
            actor_from_telegram(message.from_user),
            MasterProfileUpdate.model_validate({field: value}),
            correlation_id=correlation_id,
        )
    except (DomainError, ValidationError) as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    await message.answer(_render_admin(profile), reply_markup=master_profile_keyboard(profile))


@router.callback_query(MasterProfileAdminCallback.filter(F.action == "edit_photo"))
async def begin_photo_edit(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(AdminMasterProfileEdit.photo)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Отправьте одно фото мастера.", reply_markup=cancel_keyboard()
        )
    await callback.answer()


@router.message(AdminMasterProfileEdit.photo, F.photo)
async def save_photo_edit(
    message: Message,
    state: FSMContext,
    master_profile_service: MasterProfileService,
    correlation_id: str,
) -> None:
    if message.from_user is None or not message.photo:
        return
    photo = message.photo[-1]
    profile = await master_profile_service.update(
        actor_from_telegram(message.from_user),
        MasterProfileUpdate(
            telegram_photo_file_id=photo.file_id,
            telegram_photo_file_unique_id=photo.file_unique_id,
        ),
        correlation_id=correlation_id,
    )
    await state.clear()
    await message.answer(_render_admin(profile), reply_markup=master_profile_keyboard(profile))


@router.message(AdminMasterProfileEdit.photo)
async def reject_non_photo(message: Message) -> None:
    await message.answer("Нужно отправить именно фото.")


@router.callback_query(MasterProfileAdminCallback.filter(F.action == "remove_photo"))
async def remove_photo(
    callback: CallbackQuery,
    master_profile_service: MasterProfileService,
    correlation_id: str,
) -> None:
    profile = await master_profile_service.update(
        actor_from_telegram(callback.from_user),
        MasterProfileUpdate(
            telegram_photo_file_id=None,
            telegram_photo_file_unique_id=None,
        ),
        correlation_id=correlation_id,
    )
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _render_admin(profile), reply_markup=master_profile_keyboard(profile)
        )
    await callback.answer("Фото удалено.")


@router.callback_query(MasterProfileAdminCallback.filter(F.action.in_({"publish", "unpublish"})))
async def change_publication(
    callback: CallbackQuery,
    callback_data: MasterProfileAdminCallback,
    master_profile_service: MasterProfileService,
    correlation_id: str,
) -> None:
    profile = await master_profile_service.set_published(
        actor_from_telegram(callback.from_user),
        callback_data.action == "publish",
        correlation_id=correlation_id,
    )
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            _render_admin(profile), reply_markup=master_profile_keyboard(profile)
        )
    await callback.answer("Статус публикации обновлён.")


@router.callback_query(MasterProfileAdminCallback.filter(F.action == "preview"))
async def preview_profile(
    callback: CallbackQuery, master_profile_service: MasterProfileService
) -> None:
    profile = await master_profile_service.get_admin(actor_from_telegram(callback.from_user))
    if isinstance(callback.message, Message):
        text = _render_public(profile)
        keyboard = master_profile_links_keyboard(profile)
        if profile.telegram_photo_file_id:
            await callback.message.answer_photo(
                profile.telegram_photo_file_id, caption=text, reply_markup=keyboard
            )
        else:
            await callback.message.answer(text, reply_markup=keyboard)
    await callback.answer()


@router.callback_query(MasterProfileAdminCallback.filter(F.action == "links"))
async def show_links(callback: CallbackQuery, master_profile_service: MasterProfileService) -> None:
    profile = await master_profile_service.get_admin(actor_from_telegram(callback.from_user))
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Публичные ссылки мастера:", reply_markup=master_links_keyboard(profile)
        )
    await callback.answer()


@router.callback_query(MasterProfileAdminCallback.filter(F.action.in_({"add_link", "edit_link"})))
async def begin_link_edit(
    callback: CallbackQuery,
    callback_data: MasterProfileAdminCallback,
    state: FSMContext,
) -> None:
    await state.set_state(AdminMasterProfileEdit.link_value)
    await state.update_data(link_id=callback_data.object_id)
    if isinstance(callback.message, Message):
        await callback.message.answer(
            "Введите: Название | https://адрес | порядок | да/нет\n"
            "Например: VK | https://vk.com/example | 10 | да",
            reply_markup=cancel_keyboard(),
        )
    await callback.answer()


@router.message(AdminMasterProfileEdit.link_value)
async def save_link_edit(
    message: Message,
    state: FSMContext,
    master_profile_service: MasterProfileService,
    correlation_id: str,
) -> None:
    if message.from_user is None:
        return
    try:
        parts = [part.strip() for part in (message.text or "").split("|")]
        if len(parts) != 4:
            raise ValueError("Нужно указать ровно четыре значения через символ |.")
        enabled = parts[3].casefold()
        if enabled not in {"да", "нет"}:
            raise ValueError("Последнее значение должно быть «да» или «нет».")
        values = MasterPublicLinkInput(
            label=parts[0], url=parts[1], sort_order=int(parts[2]), is_active=enabled == "да"
        )
        link_id = int((await state.get_data()).get("link_id", 0))
        actor = actor_from_telegram(message.from_user)
        if link_id:
            await master_profile_service.update_link(
                actor, link_id, values, correlation_id=correlation_id
            )
        else:
            await master_profile_service.add_link(actor, values, correlation_id=correlation_id)
    except (DomainError, ValidationError, ValueError) as exc:
        await message.answer(str(exc))
        return
    await state.clear()
    profile = await master_profile_service.get_admin(actor_from_telegram(message.from_user))
    await message.answer("Ссылки обновлены.", reply_markup=master_links_keyboard(profile))


@router.callback_query(MasterProfileAdminCallback.filter(F.action == "delete_link"))
async def delete_link(
    callback: CallbackQuery,
    callback_data: MasterProfileAdminCallback,
    master_profile_service: MasterProfileService,
    correlation_id: str,
) -> None:
    try:
        await master_profile_service.delete_link(
            actor_from_telegram(callback.from_user),
            callback_data.object_id,
            correlation_id=correlation_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    profile = await master_profile_service.get_admin(actor_from_telegram(callback.from_user))
    if isinstance(callback.message, Message):
        await callback.message.edit_text(
            "Публичные ссылки мастера:", reply_markup=master_links_keyboard(profile)
        )
    await callback.answer("Ссылка удалена.")


def _render_admin(profile: MasterProfileView) -> str:
    status = "опубликовано" if profile.is_published else "черновик"
    photo = "добавлено" if profile.telegram_photo_file_id else "нет"
    return (
        f"<b>Информация о мастере</b> · {status}\n"
        f"Имя: {escape(profile.display_name)}\n"
        f"Описание: {escape(profile.bio) if profile.bio else '—'}\n"
        f"Фото: {photo}\n"
        f"Адрес: {escape(profile.address) if profile.address else '—'}\n"
        f"Ссылок: {len(profile.links)}"
    )


def _render_public(profile: MasterProfileView) -> str:
    parts = [f"<b>{escape(profile.display_name)}</b>"]
    if profile.bio:
        parts.append(escape(profile.bio))
    if profile.address:
        parts.append(f"📍 {escape(profile.address)}")
    return "\n\n".join(parts)
