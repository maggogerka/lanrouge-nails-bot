"""Real PostgreSQL guarantees for bootstrap immutability and staff reassignment."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import DBAPIError, IntegrityError

from app.database import Database
from app.database.models import (
    Appointment,
    AvailabilityWindow,
    Service,
    StaffMember,
    StaffServiceAssignment,
    User,
)
from app.domain.enums import (
    AppointmentStatus,
    AvailabilityWindowStatus,
    PaymentMode,
    StaffRole,
    UserRole,
)
from app.schemas.authorization import StaffContext
from app.services.authorization_service import AuthorizationService

NOW = datetime(2030, 8, 11, 9, tzinfo=UTC)


async def seed_staff(database: Database) -> tuple[StaffContext, int, int]:
    async with database.sessions() as session:
        bootstrap_user = User(
            telegram_id=700001,
            first_name="Bootstrap",
            role=UserRole.ADMIN,
        )
        source_user = User(telegram_id=700002, first_name="Source", role=UserRole.ADMIN)
        target_user = User(telegram_id=700003, first_name="Target", role=UserRole.ADMIN)
        session.add_all([bootstrap_user, source_user, target_user])
        await session.flush()
        bootstrap = StaffMember(
            business_id=1,
            user_id=bootstrap_user.id,
            display_name="Bootstrap",
            role=StaffRole.OWNER,
            is_active=True,
            is_bookable=False,
            is_bootstrap_owner=True,
        )
        source = StaffMember(
            business_id=1,
            user_id=source_user.id,
            display_name="Source",
            role=StaffRole.MASTER,
            is_active=False,
            is_bookable=False,
            archived_at=NOW,
        )
        target = StaffMember(
            business_id=1,
            user_id=target_user.id,
            display_name="Target",
            role=StaffRole.MASTER,
            is_active=True,
            is_bookable=True,
        )
        session.add_all([bootstrap, source, target])
        await session.flush()
        context = StaffContext(
            business_id=1,
            staff_member_id=bootstrap.id,
            user_id=bootstrap_user.id,
            telegram_id=bootstrap_user.telegram_id,
            display_name=bootstrap.display_name,
            role=StaffRole.OWNER,
            is_bookable=False,
            is_bootstrap_owner=True,
        )
        await session.commit()
        return context, source.id, target.id


@pytest.mark.asyncio
async def test_database_rejects_bootstrap_demotion_block_and_duplicate(
    integration_database: Database,
) -> None:
    context, _source_id, _target_id = await seed_staff(integration_database)

    async with integration_database.sessions() as session:
        with pytest.raises(DBAPIError, match="bootstrap owner is immutable"):
            await session.execute(
                update(StaffMember)
                .where(StaffMember.id == context.staff_member_id)
                .values(role=StaffRole.MASTER)
            )
            await session.commit()
        await session.rollback()

    async with integration_database.sessions() as session:
        with pytest.raises(DBAPIError, match="cannot be replaced or blocked"):
            await session.execute(
                update(User).where(User.id == context.user_id).values(is_blocked=True)
            )
            await session.commit()
        await session.rollback()

    async with integration_database.sessions() as session:
        another_user = User(telegram_id=700004, role=UserRole.ADMIN)
        session.add(another_user)
        await session.flush()
        session.add(
            StaffMember(
                business_id=1,
                user_id=another_user.id,
                display_name="Second bootstrap",
                role=StaffRole.OWNER,
                is_active=True,
                is_bookable=False,
                is_bootstrap_owner=True,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()


@pytest.mark.asyncio
async def test_reassign_future_appointment_uses_exact_target_window(
    integration_database: Database,
) -> None:
    context, source_id, target_id = await seed_staff(integration_database)
    async with integration_database.sessions() as session:
        service = Service(
            business_id=1,
            name="Consultation",
            price=Decimal("2500.00"),
            duration_min_minutes=60,
            duration_max_minutes=60,
            is_active=True,
        )
        session.add(service)
        await session.flush()
        session.add(
            StaffServiceAssignment(
                business_id=1,
                staff_member_id=target_id,
                service_id=service.id,
                online_booking_enabled=True,
                is_active=True,
            )
        )
        old_window = AvailabilityWindow(
            business_id=1,
            staff_member_id=source_id,
            start_at=NOW,
            end_at=NOW.replace(hour=10),
            status=AvailabilityWindowStatus.BOOKED,
            created_by=context.user_id,
        )
        new_window = AvailabilityWindow(
            business_id=1,
            staff_member_id=target_id,
            start_at=NOW,
            end_at=NOW.replace(hour=10),
            status=AvailabilityWindowStatus.OPEN,
            created_by=context.user_id,
        )
        session.add_all([old_window, new_window])
        await session.flush()
        appointment = Appointment(
            business_id=1,
            staff_member_id=source_id,
            client_id=context.user_id,
            window_id=old_window.id,
            service_id=service.id,
            service_name_snapshot=service.name,
            master_name_snapshot="Source",
            price_snapshot=service.price,
            prepayment_snapshot=Decimal("0"),
            currency_snapshot="RUB",
            payment_mode_snapshot=PaymentMode.DISABLED,
            duration_min_snapshot=60,
            duration_max_snapshot=60,
            scheduled_start_at=old_window.start_at,
            scheduled_end_at=old_window.end_at,
            status=AppointmentStatus.CONFIRMED,
        )
        session.add(appointment)
        await session.commit()
        appointment_id = appointment.id
        old_window_id = old_window.id
        new_window_id = new_window.id

    authorization = AuthorizationService(integration_database.sessions)
    moved = await authorization.reassign_future_appointments(
        context,
        source_id,
        target_id,
        now=NOW.replace(year=2029),
        correlation_id="staff-reassign-test",
    )

    assert moved == 1
    async with integration_database.sessions() as session:
        appointment = await session.scalar(
            select(Appointment).where(Appointment.id == appointment_id)
        )
        old_window = await session.get(AvailabilityWindow, old_window_id)
        new_window = await session.get(AvailabilityWindow, new_window_id)
        assert appointment is not None and appointment.staff_member_id == target_id
        assert appointment.window_id == new_window_id
        assert appointment.master_name_snapshot == "Target"
        assert old_window is not None and old_window.status is AvailabilityWindowStatus.OPEN
        assert new_window is not None and new_window.status is AvailabilityWindowStatus.BOOKED
