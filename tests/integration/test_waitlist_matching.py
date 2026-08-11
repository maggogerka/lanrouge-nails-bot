"""PostgreSQL waitlist filtering and duplicate prevention."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.database import Database
from app.database.models import (
    AvailabilityWindow,
    BusinessSettings,
    Service,
    User,
    WaitlistEntry,
    WaitlistNotification,
)
from app.domain.enums import AvailabilityWindowStatus, UserRole, WaitlistStatus
from app.repositories import SqlAlchemyUnitOfWork
from app.services.waitlist_matching import enqueue_waitlist_matches

NOW = datetime(2026, 7, 22, 9, tzinfo=UTC)


@pytest.mark.asyncio
async def test_matching_filters_preferences_and_prevents_duplicate_jobs(
    integration_database: Database,
) -> None:
    async with integration_database.sessions() as session:
        users = [
            User(
                telegram_id=100 + index,
                first_name=f"Client {index}",
                privacy_consent_at=NOW,
                is_blocked=index == 6,
                role=UserRole.CLIENT,
            )
            for index in range(1, 7)
        ]
        admin = User(telegram_id=900, first_name="Admin", role=UserRole.ADMIN)
        session.add_all([admin, *users])
        await session.flush()
        fitting = Service(
            business_id=1,
            name="Маникюр",
            price=Decimal("2500"),
            duration_min_minutes=120,
            duration_max_minutes=180,
            is_active=True,
        )
        too_long = Service(
            business_id=1,
            name="Длинная услуга",
            price=Decimal("3500"),
            duration_min_minutes=200,
            duration_max_minutes=240,
            is_active=True,
        )
        session.add_all([fitting, too_long])
        await session.flush()
        target_date = date(2026, 7, 23)
        common = {
            "date_from": target_date,
            "date_to": target_date,
            "preferred_dates": [],
            "expires_at": NOW + timedelta(days=2),
        }
        session.add_all(
            [
                WaitlistEntry(
                    business_id=1,
                    client_id=users[0].id,
                    service_id=fitting.id,
                    status=WaitlistStatus.ACTIVE,
                    preferred_time_from=time(9),
                    preferred_time_to=time(13),
                    **common,
                ),
                WaitlistEntry(
                    business_id=1,
                    client_id=users[1].id,
                    service_id=fitting.id,
                    status=WaitlistStatus.ACTIVE,
                    preferred_time_from=time(13),
                    preferred_time_to=time(17),
                    **common,
                ),
                WaitlistEntry(
                    business_id=1,
                    client_id=users[2].id,
                    service_id=too_long.id,
                    status=WaitlistStatus.ACTIVE,
                    **common,
                ),
                WaitlistEntry(
                    business_id=1,
                    client_id=users[3].id,
                    service_id=fitting.id,
                    status=WaitlistStatus.CANCELLED,
                    **common,
                ),
                WaitlistEntry(
                    business_id=1,
                    client_id=users[4].id,
                    service_id=fitting.id,
                    status=WaitlistStatus.EXPIRED,
                    expires_at=NOW - timedelta(minutes=1),
                    date_from=target_date,
                    date_to=target_date,
                    preferred_dates=[],
                ),
                WaitlistEntry(
                    business_id=1,
                    client_id=users[5].id,
                    service_id=fitting.id,
                    status=WaitlistStatus.ACTIVE,
                    **common,
                ),
            ]
        )
        window = AvailabilityWindow(
            business_id=1,
            staff_member_id=1,
            start_at=datetime(2026, 7, 23, 7, tzinfo=UTC),
            end_at=datetime(2026, 7, 23, 10, tzinfo=UTC),
            status=AvailabilityWindowStatus.OPEN,
            created_by=admin.id,
        )
        session.add(window)
        await session.commit()

    async with SqlAlchemyUnitOfWork(integration_database.sessions) as uow:
        settings = await uow.settings.get()
        target_window = await uow.windows.get(1)
        assert isinstance(settings, BusinessSettings)
        assert target_window is not None
        assert await enqueue_waitlist_matches(uow, target_window, settings, now=NOW) == 1
        await uow.commit()

    async with SqlAlchemyUnitOfWork(integration_database.sessions) as uow:
        settings = await uow.settings.get()
        target_window = await uow.windows.get(1)
        assert settings is not None and target_window is not None
        assert await enqueue_waitlist_matches(uow, target_window, settings, now=NOW) == 0
        await uow.commit()

    async with integration_database.sessions() as session:
        count = await session.scalar(select(func.count(WaitlistNotification.id)))
        assert count == 1
