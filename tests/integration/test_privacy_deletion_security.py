"""Real PostgreSQL contracts for revocation-before-anonymization and idempotency."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.database import Database
from app.database.models import BusinessClient, DataDeletionRequest, StaffMember, User
from app.domain.enums import DataDeletionRequestStatus, StaffRole, UserRole
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.service import AdminActor
from app.services.authorization_service import AuthorizationService
from app.services.privacy_service import PrivacyDeletionRuntimeService

NOW = datetime(2030, 8, 11, 12, tzinfo=UTC)


async def seed_subjects(database: Database) -> tuple[int, int, int, int]:
    async with database.sessions() as session:
        bootstrap_user = User(telegram_id=810001, first_name="Bootstrap", role=UserRole.ADMIN)
        invited_user = User(
            telegram_id=810002,
            username="personal_username",
            first_name="Personal",
            last_name="Name",
            phone="+79990000000",
            role=UserRole.ADMIN,
        )
        session.add_all([bootstrap_user, invited_user])
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
        invited = StaffMember(
            business_id=1,
            user_id=invited_user.id,
            display_name="Invited owner",
            bio="private bio",
            telegram_photo_file_id="private-file-id",
            telegram_photo_file_unique_id="private-unique-id",
            role=StaffRole.OWNER,
            is_active=False,
            is_bookable=False,
            archived_at=NOW,
        )
        session.add_all([bootstrap, invited])
        await session.flush()
        bootstrap_client = BusinessClient(business_id=1, user_id=bootstrap_user.id, is_active=True)
        invited_client = BusinessClient(business_id=1, user_id=invited_user.id, is_active=True)
        session.add_all([bootstrap_client, invited_client])
        await session.flush()
        bootstrap_request = DataDeletionRequest(
            business_id=1,
            business_client_id=bootstrap_client.id,
            status=DataDeletionRequestStatus.APPROVED,
            requested_at=NOW,
        )
        invited_request = DataDeletionRequest(
            business_id=1,
            business_client_id=invited_client.id,
            status=DataDeletionRequestStatus.APPROVED,
            requested_at=NOW,
        )
        session.add_all([bootstrap_request, invited_request])
        await session.commit()
        return (
            bootstrap_user.telegram_id,
            bootstrap_request.id,
            invited_request.id,
            invited_user.id,
        )


@pytest.mark.asyncio
async def test_bootstrap_is_blocked_and_revoked_owner_is_anonymized_once(
    integration_database: Database,
) -> None:
    telegram_id, bootstrap_request_id, invited_request_id, invited_user_id = await seed_subjects(
        integration_database
    )
    service = PrivacyDeletionRuntimeService(
        lambda: SqlAlchemyUnitOfWork(integration_database.sessions),
        AuthorizationService(integration_database.sessions),
    )
    actor = AdminActor(telegram_id=telegram_id)

    blocked = await service.execute_anonymization(
        actor, bootstrap_request_id, confirmed=True, now=NOW
    )
    assert not blocked.completed
    assert blocked.error_codes == ("bootstrap_owner",)

    first, repeated = await asyncio.gather(
        service.execute_anonymization(actor, invited_request_id, confirmed=True, now=NOW),
        service.execute_anonymization(actor, invited_request_id, confirmed=True, now=NOW),
    )
    assert first.completed and repeated.completed

    async with integration_database.sessions() as session:
        user = await session.get(User, invited_user_id)
        request = await session.get(DataDeletionRequest, invited_request_id)
        staff = await session.scalar(
            select(StaffMember).where(StaffMember.display_name == "[anonymized]")
        )
        assert user is not None
        assert user.telegram_id < 0
        assert user.username is None and user.phone is None
        assert user.first_name is None and user.last_name is None
        assert request is not None and request.status is DataDeletionRequestStatus.COMPLETED
        assert request.attempt_count == 1
        assert staff is not None and staff.user_id is None
        assert staff.telegram_photo_file_id is None
