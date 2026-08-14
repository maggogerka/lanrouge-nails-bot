"""Physical workstation administration and scope tests."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.enums import StaffRole
from app.domain.errors import WorkstationStateError
from app.schemas.authorization import StaffContext
from app.schemas.workstation import WorkstationCreate
from app.services.workstation_service import WorkstationService


def owner() -> StaffContext:
    return StaffContext(
        business_id=1,
        staff_member_id=1,
        user_id=10,
        telegram_id=100,
        display_name="Владелец",
        role=StaffRole.OWNER,
        is_bookable=True,
    )


def uow() -> MagicMock:
    result = MagicMock()
    result.business_id = 1
    result.__aenter__ = AsyncMock(return_value=result)
    result.__aexit__ = AsyncMock(return_value=None)
    result.workstations.get_by_name = AsyncMock(return_value=None)
    result.workstations.list_service_rows = AsyncMock(return_value=[])
    result.workstations.has_future_active_windows = AsyncMock(return_value=False)
    result.workstations.set_service_enabled = AsyncMock()
    result.audit.add = AsyncMock()
    result.commit = AsyncMock()

    async def add(row: object) -> object:
        row.id = 7  # type: ignore[attr-defined]
        return row

    result.workstations.add = AsyncMock(side_effect=add)
    return result


@pytest.mark.asyncio
async def test_owner_creates_tenant_scoped_workstation_and_audit() -> None:
    unit_of_work = uow()
    authorization = SimpleNamespace(authorize=AsyncMock(return_value=owner()))
    service = WorkstationService(lambda: unit_of_work, authorization)  # type: ignore[arg-type]

    created = await service.create(
        owner(),
        WorkstationCreate(name="  Маникюрный стол 1  "),
        correlation_id="workstation-create",
    )

    assert created.id == 7
    assert created.name == "Маникюрный стол 1"
    assert unit_of_work.workstations.add.await_args.args[0].business_id == 1
    assert unit_of_work.audit.add.await_args.kwargs["action"] == "workstation.created"
    unit_of_work.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_service_mapping_cannot_be_removed_while_future_window_uses_it() -> None:
    unit_of_work = uow()
    unit_of_work.workstations.get = AsyncMock(return_value=SimpleNamespace(id=7, is_active=True))
    unit_of_work.services.get = AsyncMock(return_value=SimpleNamespace(id=3, is_active=True))
    unit_of_work.workstations.has_future_active_windows.return_value = True
    authorization = SimpleNamespace(authorize=AsyncMock(return_value=owner()))
    service = WorkstationService(lambda: unit_of_work, authorization)  # type: ignore[arg-type]

    with pytest.raises(WorkstationStateError, match="будущие окна"):
        await service.set_service_enabled(
            owner(),
            7,
            3,
            enabled=False,
            now=datetime(2026, 8, 14, tzinfo=UTC),
        )

    unit_of_work.workstations.set_service_enabled.assert_not_awaited()
    unit_of_work.commit.assert_not_awaited()
