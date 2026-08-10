"""Owner controls and runtime prerequisites for feature flags."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.enums import StaffRole
from app.domain.errors import AuthorizationError, FeatureDisabledError
from app.keyboards.admin.features import feature_flags_keyboard
from app.schemas.authorization import StaffContext
from app.schemas.features import FeatureName, FeatureSnapshot
from app.services.feature_flag_service import FeatureFlagService, FeaturePrerequisites


def actor(role: StaffRole = StaffRole.OWNER) -> StaffContext:
    return StaffContext(
        business_id=1,
        staff_member_id=2,
        user_id=3,
        telegram_id=4,
        display_name="Владелец",
        role=role,
        is_bookable=False,
    )


def snapshot(**updates: bool) -> FeatureSnapshot:
    values = {field: False for field in FeatureSnapshot.model_fields}
    values.update(updates)
    return FeatureSnapshot.model_validate(values)


def uow_for(flags: FeatureSnapshot) -> MagicMock:
    unit_of_work = MagicMock()
    unit_of_work.business_id = 1
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    row = SimpleNamespace(**flags.model_dump())
    unit_of_work.features.get = AsyncMock(return_value=row)
    unit_of_work.features.flush = AsyncMock()
    unit_of_work.audit.add = AsyncMock()
    unit_of_work.commit = AsyncMock()
    return unit_of_work


@pytest.mark.asyncio
async def test_owner_toggle_is_persisted_and_audited() -> None:
    unit_of_work = uow_for(snapshot())
    service = FeatureFlagService(lambda: unit_of_work)  # type: ignore[arg-type]

    result = await service.set_enabled(
        actor(),
        FeatureName.WAITLIST,
        True,
        correlation_id="corr-feature",
    )

    assert result.waitlist
    unit_of_work.audit.add.assert_awaited_once()
    assert unit_of_work.audit.add.await_args.kwargs["correlation_id"] == "corr-feature"
    unit_of_work.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_manager_cannot_change_feature_even_with_forged_callback() -> None:
    unit_of_work = uow_for(snapshot())
    service = FeatureFlagService(lambda: unit_of_work)  # type: ignore[arg-type]

    with pytest.raises(AuthorizationError):
        await service.set_enabled(actor(StaffRole.MANAGER), FeatureName.WAITLIST, True)

    unit_of_work.features.get.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "feature",
    [FeatureName.YOOKASSA_PAYMENTS, FeatureName.MINI_APP],
)
async def test_external_feature_cannot_be_enabled_without_runtime_secrets(
    feature: FeatureName,
) -> None:
    unit_of_work = uow_for(snapshot())
    service = FeatureFlagService(lambda: unit_of_work)  # type: ignore[arg-type]

    with pytest.raises(FeatureDisabledError):
        await service.set_enabled(actor(), feature, True)

    unit_of_work.features.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_configured_yookassa_can_be_enabled() -> None:
    unit_of_work = uow_for(snapshot())
    service = FeatureFlagService(
        lambda: unit_of_work,  # type: ignore[arg-type]
        FeaturePrerequisites(yookassa_ready=True),
    )

    result = await service.set_enabled(actor(), FeatureName.YOOKASSA_PAYMENTS, True)

    assert result.yookassa_payments


def test_manager_feature_screen_is_read_only() -> None:
    keyboard = feature_flags_keyboard(snapshot(waitlist=True), can_manage=False)

    assert all(
        button.callback_data == "feature_readonly"
        for row in keyboard.inline_keyboard
        for button in row
    )
