"""Permission-aware business feature controls."""

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.domain.errors import DomainError
from app.keyboards.admin.features import FeatureAdminCallback, feature_flags_keyboard
from app.keyboards.admin.main import ADMIN_FEATURES_TEXT
from app.schemas.authorization import StaffContext, StaffPermission
from app.services.feature_flag_service import FeatureFlagService

router = Router(name="admin.features")


def _can_manage(actor: StaffContext) -> bool:
    return actor.has_permission(StaffPermission.MANAGE_FEATURE_FLAGS)


async def _show(
    message: Message,
    service: FeatureFlagService,
    actor: StaffContext,
) -> None:
    snapshot = await service.snapshot()
    await message.answer(
        "<b>Функции бота</b>\n"
        "Изменения применяются к меню, прямым callback и фоновым задачам.\n"
        "Нажмите на функцию, чтобы изменить состояние.",
        reply_markup=feature_flags_keyboard(snapshot, can_manage=_can_manage(actor)),
    )


@router.message(F.text == ADMIN_FEATURES_TEXT)
async def show_features(
    message: Message,
    feature_flag_service: FeatureFlagService,
    staff_context: StaffContext,
) -> None:
    await _show(message, feature_flag_service, staff_context)


@router.callback_query(F.data == "feature_readonly")
async def readonly_feature(callback: CallbackQuery) -> None:
    await callback.answer("Изменять функции может только владелец.", show_alert=True)


@router.callback_query(FeatureAdminCallback.filter())
async def toggle_feature(
    callback: CallbackQuery,
    callback_data: FeatureAdminCallback,
    feature_flag_service: FeatureFlagService,
    staff_context: StaffContext,
    correlation_id: str,
) -> None:
    try:
        snapshot = await feature_flag_service.set_enabled(
            staff_context,
            callback_data.name,
            callback_data.enabled,
            correlation_id=correlation_id,
        )
    except DomainError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    if isinstance(callback.message, Message):
        await callback.message.edit_reply_markup(
            reply_markup=feature_flags_keyboard(snapshot, can_manage=True)
        )
    await callback.answer("Функция включена." if callback_data.enabled else "Функция выключена.")
