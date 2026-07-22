"""Authorized, versioned business settings mutation tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.schemas.service import AdminActor
from app.schemas.settings import BusinessSettingsPatch
from app.services.settings_service import SettingsService
from tests.unit.test_appointment_service import settings


def build_uow() -> MagicMock:
    unit_of_work = MagicMock()
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    unit_of_work.users.get_or_create_admin = AsyncMock(return_value=SimpleNamespace(id=9))
    unit_of_work.settings.get = AsyncMock(return_value=settings())
    unit_of_work.session.flush = AsyncMock()
    unit_of_work.audit.add = AsyncMock()
    unit_of_work.commit = AsyncMock()
    return unit_of_work


def test_reminder_offsets_must_be_unique_and_non_empty() -> None:
    with pytest.raises(ValidationError, match="unique"):
        BusinessSettingsPatch(reminder_offsets_minutes=[60, 60])


@pytest.mark.asyncio
async def test_setting_update_locks_row_increments_version_and_audits() -> None:
    unit_of_work = build_uow()
    service = SettingsService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    updated = await service.update(
        AdminActor(telegram_id=900),
        BusinessSettingsPatch(max_appointments_per_day=3),
        correlation_id="request-3",
    )

    assert updated.max_appointments_per_day == 3
    assert updated.version == 2
    unit_of_work.settings.get.assert_awaited_once_with(for_update=True)
    assert unit_of_work.audit.add.await_args.kwargs["changes"]["max_appointments_per_day"] == {
        "before": 2,
        "after": 3,
    }
    unit_of_work.commit.assert_awaited_once()
