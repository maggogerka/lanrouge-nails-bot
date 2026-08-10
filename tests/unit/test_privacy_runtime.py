"""Security contracts for the runtime privacy-deletion workflow."""

from __future__ import annotations

from datetime import UTC, datetime
from types import TracebackType
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.models.business import BusinessClient
from app.database.models.privacy import DataDeletionRequest
from app.database.models.user import User
from app.domain.enums import DataDeletionRequestStatus, StaffRole
from app.domain.errors import AuthorizationError, EntityNotFoundError
from app.domain.privacy import PrivacyStateError
from app.repositories.privacy_repository import (
    AnonymizationBlockers,
    AnonymizationMutationCounts,
)
from app.schemas.authorization import StaffContext, StaffPermission
from app.schemas.service import AdminActor
from app.services.authorization_service import AuthorizationService
from app.services.privacy_service import (
    DeletionRequestNotificationService,
    DeletionRequestOutcome,
    PrivacyDeletionRuntimeService,
)

NOW = datetime(2026, 8, 10, 9, tzinfo=UTC)


class FakeUnitOfWork:
    def __init__(self, *, business_id: int = 1) -> None:
        self.business_id = business_id
        self.privacy = MagicMock()
        self.audit = MagicMock()
        self.audit.add = AsyncMock()
        self.commit = AsyncMock()

    async def __aenter__(self) -> FakeUnitOfWork:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback


def actor() -> AdminActor:
    return AdminActor(telegram_id=700)


def staff_context(*, role: StaffRole = StaffRole.OWNER, telegram_id: int = 700) -> StaffContext:
    return StaffContext(
        business_id=1,
        staff_member_id=8,
        user_id=80,
        telegram_id=telegram_id,
        display_name="Owner",
        role=role,
        is_bookable=False,
    )


def authorization_mock() -> MagicMock:
    service = MagicMock()
    service.authorize = AsyncMock(return_value=staff_context())
    service.list_active_staff = AsyncMock()
    return service


def runtime(
    unit_of_work: FakeUnitOfWork,
    authorization: MagicMock,
) -> PrivacyDeletionRuntimeService:
    return PrivacyDeletionRuntimeService(
        cast(Any, lambda: unit_of_work),
        cast(AuthorizationService, authorization),
    )


def request(status: DataDeletionRequestStatus) -> DataDeletionRequest:
    return DataDeletionRequest(
        id=23,
        business_id=1,
        business_client_id=5,
        status=status,
        requested_at=NOW,
        anonymization_plan={},
        anonymization_result={},
    )


@pytest.mark.asyncio
async def test_role_denial_happens_before_repository_access() -> None:
    unit_of_work = FakeUnitOfWork()
    authorization = authorization_mock()
    authorization.authorize.side_effect = AuthorizationError("denied")
    factory = MagicMock(return_value=unit_of_work)
    service = PrivacyDeletionRuntimeService(
        cast(Any, factory),
        cast(AuthorizationService, authorization),
    )

    with pytest.raises(AuthorizationError):
        await service.list_requests(actor())

    factory.assert_not_called()
    authorization.authorize.assert_awaited_once_with(
        business_id=1,
        telegram_id=700,
        permission=StaffPermission.HANDLE_DATA_DELETION,
    )


@pytest.mark.asyncio
async def test_request_id_from_another_business_is_not_exposed() -> None:
    unit_of_work = FakeUnitOfWork()
    unit_of_work.privacy.get_deletion_request = AsyncMock(return_value=None)
    service = runtime(unit_of_work, authorization_mock())

    with pytest.raises(EntityNotFoundError):
        await service.get_request(actor(), 999)

    unit_of_work.privacy.get_deletion_request.assert_awaited_once_with(999)


@pytest.mark.asyncio
async def test_irreversible_steps_require_explicit_confirmation() -> None:
    authorization = authorization_mock()
    service = runtime(FakeUnitOfWork(), authorization)

    with pytest.raises(PrivacyStateError, match="explicit confirmation"):
        await service.start_review(actor(), 23, confirmed=False)
    with pytest.raises(PrivacyStateError, match="explicit confirmation"):
        await service.approve(actor(), 23, confirmed=False)
    with pytest.raises(PrivacyStateError, match="explicit confirmation"):
        await service.execute_anonymization(actor(), 23, confirmed=False)

    authorization.authorize.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("blockers", "expected_code"),
    [
        (AnonymizationBlockers(active_staff_memberships=1), "active_staff_membership"),
        (
            AnonymizationBlockers(other_active_business_memberships=1),
            "other_active_business_membership",
        ),
    ],
)
async def test_active_membership_blocks_all_mutations_and_returns_to_review(
    blockers: AnonymizationBlockers,
    expected_code: str,
) -> None:
    unit_of_work = FakeUnitOfWork()
    deletion_request = request(DataDeletionRequestStatus.APPROVED)
    unit_of_work.privacy.get_deletion_request = AsyncMock(return_value=deletion_request)
    unit_of_work.privacy.get_client = AsyncMock(
        return_value=BusinessClient(id=5, business_id=1, user_id=11)
    )
    unit_of_work.privacy.get_user = AsyncMock(return_value=User(id=11, telegram_id=111))
    unit_of_work.privacy.anonymization_blockers = AsyncMock(return_value=blockers)
    unit_of_work.privacy.add_deletion_event = AsyncMock()
    unit_of_work.privacy.flush = AsyncMock()
    unit_of_work.privacy.anonymize_client_data = AsyncMock()
    service = runtime(unit_of_work, authorization_mock())

    outcome = await service.execute_anonymization(actor(), 23, confirmed=True, now=NOW)

    assert not outcome.completed
    assert outcome.error_codes == (expected_code,)
    assert outcome.request.status is DataDeletionRequestStatus.IN_REVIEW
    assert outcome.request.retention_reason_code == expected_code
    unit_of_work.privacy.anonymize_client_data.assert_not_awaited()
    unit_of_work.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_completion_retains_financial_and_appointment_snapshots() -> None:
    unit_of_work = FakeUnitOfWork()
    deletion_request = request(DataDeletionRequestStatus.APPROVED)
    unit_of_work.privacy.get_deletion_request = AsyncMock(return_value=deletion_request)
    unit_of_work.privacy.get_client = AsyncMock(
        return_value=BusinessClient(id=5, business_id=1, user_id=11)
    )
    unit_of_work.privacy.get_user = AsyncMock(return_value=User(id=11, telegram_id=111))
    unit_of_work.privacy.anonymization_blockers = AsyncMock(return_value=AnonymizationBlockers())
    unit_of_work.privacy.latest_consent = AsyncMock(return_value=None)
    unit_of_work.privacy.anonymize_client_data = AsyncMock(
        return_value=AnonymizationMutationCounts(
            identities_anonymized=2,
            notes_anonymized=4,
            comments_anonymized=3,
            reviews_anonymized=2,
            reference_links_removed=1,
            deliveries_cancelled=6,
            appointment_snapshots_retained=7,
            financial_snapshots_retained=5,
        )
    )
    unit_of_work.privacy.add_deletion_event = AsyncMock()
    unit_of_work.privacy.flush = AsyncMock()
    service = runtime(unit_of_work, authorization_mock())

    outcome = await service.execute_anonymization(actor(), 23, confirmed=True, now=NOW)

    assert outcome.completed
    assert deletion_request.status is DataDeletionRequestStatus.COMPLETED
    assert deletion_request.anonymization_result["appointment_snapshots_retained"] == 7
    assert deletion_request.anonymization_result["financial_snapshots_retained"] == 5
    assert deletion_request.anonymization_result["comments_anonymized"] == 3
    assert deletion_request.anonymization_result["deliveries_cancelled"] == 6
    assert deletion_request.retention_reason == "legal_accounting_and_service_history"
    unit_of_work.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_notification_failure_does_not_change_submission_truth() -> None:
    authorization = authorization_mock()
    authorization.list_active_staff.return_value = (
        staff_context(telegram_id=700),
        staff_context(role=StaffRole.MANAGER, telegram_id=701),
    )
    sender = MagicMock()
    sender.send_message = AsyncMock(side_effect=(None, RuntimeError("telegram unavailable")))
    service = DeletionRequestNotificationService(cast(AuthorizationService, authorization))
    persisted = DeletionRequestOutcome(
        request=request(DataDeletionRequestStatus.REQUESTED),
        created=True,
    )

    notification = await service.notify(
        sender,
        business_id=persisted.request.business_id,
        request_id=persisted.request.id,
    )

    assert persisted.created
    assert notification.eligible_count == 2
    assert notification.delivered_count == 1
    assert notification.failed_count == 1
    authorization.list_active_staff.assert_awaited_once_with(
        business_id=1,
        roles=(StaffRole.OWNER, StaffRole.MANAGER),
    )
    messages = [call.args[1] for call in sender.send_message.await_args_list]
    assert all("#23" in text for text in messages)
    assert all("клиент" not in text.lower() for text in messages)
