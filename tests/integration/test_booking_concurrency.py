"""Real PostgreSQL locking tests for the booking critical section."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.database import Database
from app.database.models import (
    Appointment,
    AvailabilityWindow,
    BusinessClient,
    Service,
    StaffMember,
    StaffServiceAssignment,
    User,
    Workstation,
    WorkstationService,
)
from app.domain.enums import AvailabilityWindowStatus, StaffRole, UserRole
from app.domain.errors import BookingConflictError
from app.repositories import SqlAlchemyUnitOfWork
from app.schemas.booking import BookingReceipt, BookingRequest, ClientActor
from app.services.booking_service import BookingService

NOW = datetime(2026, 7, 22, 9, tzinfo=UTC)


async def seed_booking_case(database: Database) -> None:
    async with database.sessions() as session:
        administrator = User(
            telegram_id=900,
            first_name="Administrator",
            role=UserRole.ADMIN,
        )
        clients = [
            User(
                telegram_id=telegram_id,
                first_name=f"Client {telegram_id}",
                privacy_consent_at=NOW,
                role=UserRole.CLIENT,
            )
            for telegram_id in (101, 202)
        ]
        session.add_all([administrator, *clients])
        await session.flush()
        session.add_all(BusinessClient(business_id=1, user_id=client.id) for client in clients)
        master = StaffMember(
            business_id=1,
            user_id=administrator.id,
            display_name="Мастер",
            role=StaffRole.MASTER,
            is_active=True,
            is_bookable=True,
        )
        session.add(master)
        await session.flush()
        service = Service(
            business_id=1,
            name="Консультация",
            description=None,
            price=Decimal("2500.00"),
            duration_min_minutes=120,
            duration_max_minutes=180,
            is_active=True,
        )
        session.add(service)
        await session.flush()
        session.add(
            StaffServiceAssignment(
                business_id=1,
                staff_member_id=master.id,
                service_id=service.id,
                online_booking_enabled=True,
                is_active=True,
            )
        )
        workstation = Workstation(
            business_id=1,
            name="Стол 1",
            is_active=True,
        )
        session.add(workstation)
        await session.flush()
        session.add(
            WorkstationService(
                business_id=1,
                workstation_id=workstation.id,
                service_id=service.id,
                is_active=True,
            )
        )
        session.add(
            AvailabilityWindow(
                business_id=1,
                staff_member_id=master.id,
                start_at=datetime(2026, 7, 23, 7, tzinfo=UTC),
                end_at=datetime(2026, 7, 23, 10, 30, tzinfo=UTC),
                status=AvailabilityWindowStatus.OPEN,
                admin_comment=None,
                created_by=administrator.id,
            )
        )
        await session.commit()


def booking_request(name: str, phone: str) -> BookingRequest:
    return BookingRequest(
        service_id=1,
        window_id=1,
        client_name=name,
        phone=phone,
        client_comment=None,
    )


@pytest.mark.asyncio
async def test_two_concurrent_clients_have_exactly_one_winner(
    integration_database: Database,
) -> None:
    await seed_booking_case(integration_database)
    booking = BookingService(
        lambda: SqlAlchemyUnitOfWork(integration_database.sessions),
        frozenset({900}),
    )

    results = await asyncio.gather(
        booking.book(
            ClientActor(telegram_id=101),
            booking_request("Анна", "+79991234567"),
            now=NOW,
        ),
        booking.book(
            ClientActor(telegram_id=202),
            booking_request("Мария", "+79997654321"),
            now=NOW,
        ),
        return_exceptions=True,
    )

    assert sum(isinstance(result, BookingReceipt) for result in results) == 1
    assert sum(isinstance(result, BookingConflictError) for result in results) == 1

    async with integration_database.sessions() as session:
        window = await session.get(AvailabilityWindow, 1)
        assert window is not None
        assert window.status is AvailabilityWindowStatus.BOOKED
        count = await session.scalar(select(func.count(Appointment.id)))
        assert count == 1


@pytest.mark.asyncio
async def test_two_masters_cannot_book_one_workstation_at_the_same_time(
    integration_database: Database,
) -> None:
    await seed_booking_case(integration_database)
    async with integration_database.sessions() as session:
        second_master = StaffMember(
            business_id=1,
            display_name="Второй мастер",
            role=StaffRole.MASTER,
            is_active=True,
            is_bookable=True,
        )
        session.add(second_master)
        await session.flush()
        session.add(
            StaffServiceAssignment(
                business_id=1,
                staff_member_id=second_master.id,
                service_id=1,
                online_booking_enabled=True,
                is_active=True,
            )
        )
        session.add(
            AvailabilityWindow(
                business_id=1,
                staff_member_id=second_master.id,
                start_at=datetime(2026, 7, 23, 7, tzinfo=UTC),
                end_at=datetime(2026, 7, 23, 10, 30, tzinfo=UTC),
                status=AvailabilityWindowStatus.OPEN,
                created_by=1,
            )
        )
        await session.commit()

    booking = BookingService(
        lambda: SqlAlchemyUnitOfWork(integration_database.sessions),
        frozenset({900}),
    )
    first = booking_request("Анна", "+79991234567")
    second = booking_request("Мария", "+79997654321").model_copy(update={"window_id": 2})

    results = await asyncio.gather(
        booking.book(ClientActor(telegram_id=101), first, now=NOW),
        booking.book(ClientActor(telegram_id=202), second, now=NOW),
        return_exceptions=True,
    )

    assert sum(isinstance(result, BookingReceipt) for result in results) == 1
    assert sum(isinstance(result, BookingConflictError) for result in results) == 1


@pytest.mark.asyncio
async def test_different_services_still_compete_for_the_same_workstation(
    integration_database: Database,
) -> None:
    await seed_booking_case(integration_database)
    async with integration_database.sessions() as session:
        second_master = StaffMember(
            business_id=1,
            display_name="Второй мастер",
            role=StaffRole.MASTER,
            is_active=True,
            is_bookable=True,
        )
        second_service = Service(
            business_id=1,
            name="Другая услуга",
            price=Decimal("1800.00"),
            duration_min_minutes=60,
            duration_max_minutes=120,
            is_active=True,
        )
        session.add_all([second_master, second_service])
        await session.flush()
        session.add(
            StaffServiceAssignment(
                business_id=1,
                staff_member_id=second_master.id,
                service_id=second_service.id,
                online_booking_enabled=True,
                is_active=True,
            )
        )
        session.add(
            WorkstationService(
                business_id=1,
                workstation_id=1,
                service_id=second_service.id,
                is_active=True,
            )
        )
        session.add(
            AvailabilityWindow(
                business_id=1,
                staff_member_id=second_master.id,
                start_at=datetime(2026, 7, 23, 7, tzinfo=UTC),
                end_at=datetime(2026, 7, 23, 10, 30, tzinfo=UTC),
                status=AvailabilityWindowStatus.OPEN,
                created_by=1,
            )
        )
        await session.commit()

    booking = BookingService(
        lambda: SqlAlchemyUnitOfWork(integration_database.sessions),
        frozenset({900}),
    )
    first = booking_request("Анна", "+79991234567")
    second = booking_request("Мария", "+79997654321").model_copy(
        update={"service_id": 2, "window_id": 2}
    )

    results = await asyncio.gather(
        booking.book(ClientActor(telegram_id=101), first, now=NOW),
        booking.book(ClientActor(telegram_id=202), second, now=NOW),
        return_exceptions=True,
    )

    assert sum(isinstance(result, BookingReceipt) for result in results) == 1
    assert sum(isinstance(result, BookingConflictError) for result in results) == 1
