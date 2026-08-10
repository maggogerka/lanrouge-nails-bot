"""Fail-closed feature and permission guards used at Telegram router boundaries."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.enums import StaffRole
from app.domain.errors import EntityNotFoundError, FeatureDisabledError
from app.filters.feature import IsAnyFeatureEnabled, IsFeatureEnabled
from app.filters.staff_permission import HasStaffPermission
from app.schemas.authorization import StaffContext, StaffPermission
from app.schemas.features import FeatureName, FeatureSnapshot


def feature_snapshot(**updates: bool) -> FeatureSnapshot:
    values = {field: False for field in FeatureSnapshot.model_fields}
    values.update(updates)
    return FeatureSnapshot.model_validate(values)


def staff_context(role: StaffRole) -> StaffContext:
    return StaffContext(
        business_id=1,
        staff_member_id=7,
        user_id=9,
        telegram_id=123,
        display_name="Сотрудник",
        role=role,
        is_bookable=role is StaffRole.MASTER,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [FeatureDisabledError("disabled"), EntityNotFoundError("missing")],
)
async def test_single_feature_guard_fails_closed(failure: Exception) -> None:
    service = MagicMock()
    service.require_enabled = AsyncMock(side_effect=failure)

    allowed = await IsFeatureEnabled(FeatureName.WAITLIST)(MagicMock(), service)

    assert not allowed


@pytest.mark.asyncio
async def test_composite_notification_guard_requires_one_persisted_flag() -> None:
    service = MagicMock()
    service.snapshot = AsyncMock(return_value=feature_snapshot(repeat_booking=True))
    guard = IsAnyFeatureEnabled(FeatureName.REMINDERS, FeatureName.REPEAT_BOOKING)

    assert await guard(MagicMock(), service)

    service.snapshot = AsyncMock(return_value=feature_snapshot())
    assert not await guard(MagicMock(), service)

    service.snapshot = AsyncMock(side_effect=EntityNotFoundError("missing"))
    assert not await guard(MagicMock(), service)


@pytest.mark.asyncio
async def test_staff_permission_guard_never_promotes_master_to_global_crud() -> None:
    guard = HasStaffPermission(StaffPermission.MANAGE_ALL_APPOINTMENTS)

    assert await guard(MagicMock(), staff_context(StaffRole.OWNER))
    assert not await guard(MagicMock(), staff_context(StaffRole.MASTER))
