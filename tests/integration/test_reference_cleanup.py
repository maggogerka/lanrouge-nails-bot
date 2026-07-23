"""Real PostgreSQL retention cleanup and idempotency."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from app.database import Database
from app.database.models import (
    Appointment,
    AppointmentReferenceMedia,
    AvailabilityWindow,
    Service,
    User,
)
from app.domain.enums import (
    AppointmentStatus,
    AvailabilityWindowStatus,
    MediaType,
    UserRole,
)
from app.repositories import SqlAlchemyUnitOfWork
from app.services.reference_cleanup_service import ReferenceCleanupService

NOW = datetime(2026, 7, 24, 9, tzinfo=UTC)


async def seed_expired_reference(database: Database) -> int:
    async with database.sessions() as session:
        administrator = User(telegram_id=900, role=UserRole.ADMIN)
        client = User(telegram_id=101, role=UserRole.CLIENT)
        session.add_all([administrator, client])
        await session.flush()
        catalog_service = Service(
            name="Маникюр",
            price=Decimal("2500.00"),
            duration_min_minutes=120,
            duration_max_minutes=180,
            is_active=True,
        )
        session.add(catalog_service)
        await session.flush()
        window = AvailabilityWindow(
            start_at=NOW - timedelta(days=31, hours=3),
            end_at=NOW - timedelta(days=31),
            status=AvailabilityWindowStatus.CLOSED,
            created_by=administrator.id,
        )
        session.add(window)
        await session.flush()
        appointment = Appointment(
            client_id=client.id,
            window_id=window.id,
            service_id=catalog_service.id,
            service_name_snapshot=catalog_service.name,
            price_snapshot=catalog_service.price,
            duration_min_snapshot=120,
            duration_max_snapshot=180,
            status=AppointmentStatus.COMPLETED,
            completed_at=NOW - timedelta(days=31),
        )
        session.add(appointment)
        await session.flush()
        reference = AppointmentReferenceMedia(
            appointment_id=appointment.id,
            telegram_file_id="telegram-file-id",
            telegram_file_unique_id="telegram-unique-id",
            media_type=MediaType.PHOTO,
            position=0,
            uploaded_by_user_id=client.id,
            expires_at=NOW - timedelta(days=1),
        )
        session.add(reference)
        await session.commit()
        return reference.id


@pytest.mark.asyncio
async def test_cleanup_dry_run_execute_and_repeat_are_safe(
    integration_database: Database,
) -> None:
    media_id = await seed_expired_reference(integration_database)
    cleanup = ReferenceCleanupService(lambda: SqlAlchemyUnitOfWork(integration_database.sessions))

    preview = await cleanup.run(dry_run=True, now=NOW)
    executed = await cleanup.run(dry_run=False, now=NOW)
    repeated = await cleanup.run(dry_run=False, now=NOW)

    assert preview.checked == 1
    assert preview.deleted == 0
    assert executed.deleted == 1
    assert repeated.checked == repeated.deleted == 0
    async with integration_database.sessions() as session:
        row = await session.get(AppointmentReferenceMedia, media_id)
        assert row is not None
        assert row.telegram_file_id is None
        assert row.telegram_file_unique_id is None
        assert row.deleted_at == NOW
        assert row.deletion_attempts == 1
