"""PostgreSQL coverage for explicit destructive aggregate cleanup."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.database import Database
from app.database.models import (
    Appointment,
    AppointmentAddonSnapshot,
    AppointmentStatusHistory,
    AvailabilityWindow,
    Service,
    ServiceAddon,
    StaffMember,
    StaffServiceAssignment,
    User,
)
from app.domain.enums import AppointmentStatus, AvailabilityWindowStatus, StaffRole, UserRole
from app.repositories.hard_delete_repository import HardDeleteRepository

START = datetime(2026, 8, 20, 7, tzinfo=UTC)
END = datetime(2026, 8, 20, 9, tzinfo=UTC)


async def seed_used_service(database: Database) -> tuple[int, int, int]:
    async with database.sessions() as session:
        owner = User(telegram_id=900, first_name="Owner", role=UserRole.ADMIN)
        client = User(telegram_id=901, first_name="Client", role=UserRole.CLIENT)
        session.add_all([owner, client])
        await session.flush()
        staff = StaffMember(
            business_id=1,
            user_id=owner.id,
            display_name="Мастер",
            role=StaffRole.MASTER,
            is_active=True,
            is_bookable=True,
        )
        service = Service(
            business_id=1,
            name="Маникюр",
            price=Decimal("2500.00"),
            duration_min_minutes=120,
            duration_max_minutes=120,
            prepayment_amount=Decimal("0.00"),
            is_active=True,
        )
        session.add_all([staff, service])
        await session.flush()
        assignment = StaffServiceAssignment(
            business_id=1,
            staff_member_id=staff.id,
            service_id=service.id,
            is_active=True,
            online_booking_enabled=True,
        )
        addon = ServiceAddon(
            business_id=1,
            service_id=service.id,
            name="Дизайн",
            price=Decimal("500.00"),
            duration_min_minutes=30,
            duration_max_minutes=30,
            is_active=True,
        )
        window = AvailabilityWindow(
            business_id=1,
            staff_member_id=staff.id,
            start_at=START,
            end_at=END,
            status=AvailabilityWindowStatus.BOOKED,
            created_by=owner.id,
        )
        session.add_all([assignment, addon, window])
        await session.flush()
        appointment = Appointment(
            business_id=1,
            staff_member_id=staff.id,
            client_id=client.id,
            window_id=window.id,
            service_id=service.id,
            service_name_snapshot=service.name,
            master_name_snapshot=staff.display_name,
            price_snapshot=service.price,
            duration_min_snapshot=120,
            duration_max_snapshot=120,
            scheduled_start_at=START,
            scheduled_end_at=END,
            status=AppointmentStatus.CONFIRMED,
        )
        session.add(appointment)
        await session.flush()
        session.add_all(
            [
                AppointmentStatusHistory(
                    appointment_id=appointment.id,
                    previous_status=None,
                    new_status=AppointmentStatus.CONFIRMED,
                    changed_by_user_id=owner.id,
                ),
                AppointmentAddonSnapshot(
                    business_id=1,
                    appointment_id=appointment.id,
                    service_addon_id=addon.id,
                    name_snapshot=addon.name,
                    price_snapshot=addon.price,
                    duration_min_snapshot=30,
                    duration_max_snapshot=30,
                    position=0,
                ),
            ]
        )
        await session.commit()
        return service.id, window.id, appointment.id


@pytest.mark.asyncio
async def test_force_delete_service_removes_dependencies_and_closes_window(
    integration_database: Database,
) -> None:
    service_id, window_id, _ = await seed_used_service(integration_database)

    async with integration_database.sessions() as session:
        repository = HardDeleteRepository(session, business_id=1)
        assert await repository.delete_service_with_history(service_id) == 1
        await session.commit()

    async with integration_database.sessions() as session:
        assert await session.get(Service, service_id) is None
        assert await session.scalar(select(func.count(Appointment.id))) == 0
        assert await session.scalar(select(func.count(ServiceAddon.id))) == 0
        assert await session.scalar(select(func.count(StaffServiceAssignment.id))) == 0
        window = await session.get(AvailabilityWindow, window_id)
        assert window is not None
        assert window.status is AvailabilityWindowStatus.CLOSED


@pytest.mark.asyncio
async def test_force_delete_window_removes_booking_but_keeps_catalog(
    integration_database: Database,
) -> None:
    service_id, window_id, _ = await seed_used_service(integration_database)

    async with integration_database.sessions() as session:
        repository = HardDeleteRepository(session, business_id=1)
        assert await repository.delete_window_with_history(window_id) == 1
        await session.commit()

    async with integration_database.sessions() as session:
        assert await session.get(AvailabilityWindow, window_id) is None
        assert await session.scalar(select(func.count(Appointment.id))) == 0
        assert await session.get(Service, service_id) is not None
        assert await session.scalar(select(func.count(ServiceAddon.id))) == 1
