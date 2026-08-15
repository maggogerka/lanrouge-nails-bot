"""Fail-closed write guards for business-scoped repositories."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.models import Appointment, AvailabilityWindow, Service
from app.repositories.appointment_repository import AppointmentRepository
from app.repositories.service_repository import ServiceRepository
from app.repositories.window_repository import WindowRepository


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("repository_type", "entity"),
    [
        (AppointmentRepository, Appointment(business_id=8)),
        (ServiceRepository, Service(business_id=8)),
        (WindowRepository, AvailabilityWindow(business_id=8)),
    ],
)
async def test_scoped_repository_rejects_foreign_entity_before_add(
    repository_type: type[AppointmentRepository] | type[ServiceRepository] | type[WindowRepository],
    entity: Appointment | Service | AvailabilityWindow,
) -> None:
    session = MagicMock()
    session.flush = AsyncMock()
    repository = repository_type(session, 7)

    with pytest.raises(ValueError, match="another business"):
        await repository.add(entity)  # type: ignore[arg-type]

    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("repository_type", "entity"),
    [
        (ServiceRepository, Service(business_id=8)),
        (WindowRepository, AvailabilityWindow(business_id=8)),
    ],
)
async def test_scoped_repository_rejects_foreign_entity_before_delete(
    repository_type: type[ServiceRepository] | type[WindowRepository],
    entity: Service | AvailabilityWindow,
) -> None:
    session = MagicMock()
    session.delete = AsyncMock()
    repository = repository_type(session, 7)

    with pytest.raises(ValueError, match="another business"):
        await repository.delete(entity)  # type: ignore[arg-type]

    session.delete.assert_not_awaited()
