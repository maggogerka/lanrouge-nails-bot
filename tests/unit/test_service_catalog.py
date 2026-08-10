"""Service catalog authorization, transaction and lifecycle tests."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.models import Service
from app.domain.errors import AuthorizationError, ServiceInUseError
from app.schemas.service import AdminActor, ServiceCreate
from app.services.service_catalog import ServiceCatalog


def actor(telegram_id: int = 101) -> AdminActor:
    return AdminActor(telegram_id=telegram_id, username="admin", first_name="Admin")


def build_uow() -> MagicMock:
    unit_of_work = MagicMock()
    unit_of_work.business_id = 1
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    unit_of_work.users.get_or_create_admin = AsyncMock(return_value=SimpleNamespace(id=5))
    unit_of_work.services.list_all = AsyncMock(return_value=[])
    unit_of_work.services.get = AsyncMock(return_value=None)
    unit_of_work.services.has_appointments = AsyncMock(return_value=False)
    unit_of_work.services.delete = AsyncMock()
    unit_of_work.service_assignments.add_assignment = AsyncMock()
    unit_of_work.audit.add = AsyncMock()
    unit_of_work.session.flush = AsyncMock()
    unit_of_work.commit = AsyncMock()
    return unit_of_work


def create_values() -> ServiceCreate:
    return ServiceCreate(
        name="Маникюр",
        description="Описание",
        price=Decimal("2500.00"),
        duration_min_minutes=120,
        duration_max_minutes=180,
    )


@pytest.mark.asyncio
async def test_non_admin_is_rejected_before_opening_uow() -> None:
    factory = MagicMock()
    catalog = ServiceCatalog(factory, frozenset({101}))

    with pytest.raises(AuthorizationError):
        await catalog.list_services(actor(202))

    factory.assert_not_called()


@pytest.mark.asyncio
async def test_create_service_audits_and_commits() -> None:
    unit_of_work = build_uow()

    async def add_service(service: Service) -> Service:
        service.id = 7
        return service

    unit_of_work.services.add = AsyncMock(side_effect=add_service)
    catalog = ServiceCatalog(lambda: unit_of_work, frozenset({101}))  # type: ignore[arg-type]

    created = await catalog.create_service(actor(), create_values(), correlation_id="request-1")

    assert created.id == 7
    assert created.price == Decimal("2500.00")
    unit_of_work.audit.add.assert_awaited_once()
    audit_call = unit_of_work.audit.add.await_args.kwargs
    assert audit_call["action"] == "service.created"
    assert audit_call["correlation_id"] == "request-1"
    unit_of_work.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_service_with_appointments_cannot_be_deleted() -> None:
    unit_of_work = build_uow()
    service = Service(
        id=7,
        name="Маникюр",
        price=Decimal("2500.00"),
        duration_min_minutes=120,
        duration_max_minutes=180,
        is_active=True,
    )
    unit_of_work.services.get = AsyncMock(return_value=service)
    unit_of_work.services.has_appointments = AsyncMock(return_value=True)
    catalog = ServiceCatalog(lambda: unit_of_work, frozenset({101}))  # type: ignore[arg-type]

    with pytest.raises(ServiceInUseError, match="архивирована"):
        await catalog.delete_unused_service(actor(), 7)

    unit_of_work.services.delete.assert_not_awaited()
    unit_of_work.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_archive_changes_state_and_writes_audit() -> None:
    unit_of_work = build_uow()
    service = Service(
        id=7,
        name="Маникюр",
        price=Decimal("2500.00"),
        duration_min_minutes=120,
        duration_max_minutes=180,
        is_active=True,
    )
    unit_of_work.services.get = AsyncMock(return_value=service)
    catalog = ServiceCatalog(lambda: unit_of_work, frozenset({101}))  # type: ignore[arg-type]

    archived = await catalog.set_active(actor(), 7, is_active=False)

    assert not archived.is_active
    assert unit_of_work.audit.add.await_args.kwargs["action"] == "service.archived"
    unit_of_work.commit.assert_awaited_once()
