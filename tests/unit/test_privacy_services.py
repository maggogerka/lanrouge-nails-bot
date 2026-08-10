"""Orchestration tests for consent, deletion and acquisition persistence boundaries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.models.business import BusinessClient
from app.database.models.crm import ConsentHistory
from app.database.models.privacy import (
    AcquisitionSource,
    ClientAcquisitionAttribution,
    DataDeletionRequest,
)
from app.domain.enums import ConsentSource, ConsentType, DataDeletionRequestStatus
from app.domain.privacy import AnonymizationResult, PolicyDocument, PrivacyStateError
from app.repositories.privacy_repository import PrivacyRepository
from app.services.acquisition_service import AcquisitionService
from app.services.privacy_service import DataDeletionService, VersionedConsentService

NOW = datetime(2026, 8, 10, 9, tzinfo=UTC)
POLICY = PolicyDocument(
    version="2026-08",
    url="https://example.test/privacy/2026-08",
    sha256="a" * 64,
)


def repository_mock() -> MagicMock:
    repository = MagicMock()
    repository.business_id = 7
    repository.get_client = AsyncMock()
    repository.get_client_by_user = AsyncMock()
    repository.latest_consent = AsyncMock()
    repository.add_consent = AsyncMock()
    repository.get_open_deletion_request = AsyncMock()
    repository.get_deletion_request = AsyncMock()
    repository.add_deletion_request = AsyncMock()
    repository.add_deletion_event = AsyncMock()
    repository.get_source_by_code = AsyncMock()
    repository.add_source = AsyncMock()
    repository.get_attribution = AsyncMock()
    repository.add_attribution = AsyncMock()
    repository.flush = AsyncMock()
    return repository


def as_repository(mock: MagicMock) -> PrivacyRepository:
    return cast(PrivacyRepository, mock)


def consent(*, accepted: bool, version: str = "2026-08") -> ConsentHistory:
    return ConsentHistory(
        id=1,
        business_id=7,
        user_id=11,
        consent_type=ConsentType.PRIVACY,
        previous_value=None,
        new_value=accepted,
        source=ConsentSource.ONBOARDING,
        policy_version=version,
        policy_url=POLICY.url,
        policy_hash=POLICY.sha256,
        revoked_at=None if accepted else NOW,
        created_at=NOW,
    )


@pytest.mark.asyncio
async def test_same_current_consent_is_idempotent() -> None:
    repository = repository_mock()
    repository.get_client_by_user.return_value = BusinessClient(
        id=5,
        business_id=7,
        user_id=11,
    )
    existing = consent(accepted=True)
    repository.latest_consent.return_value = existing
    service = VersionedConsentService(as_repository(repository))

    entry, created = await service.record(
        user_id=11,
        consent_type=ConsentType.PRIVACY,
        accepted=True,
        source=ConsentSource.ONBOARDING,
        policy=POLICY,
        now=NOW,
    )

    assert entry is existing
    assert not created
    repository.add_consent.assert_not_awaited()
    repository.get_client_by_user.assert_awaited_once_with(11, for_update=True)


@pytest.mark.asyncio
async def test_policy_version_change_creates_reconsent_proof() -> None:
    repository = repository_mock()
    repository.get_client_by_user.return_value = BusinessClient(
        id=5,
        business_id=7,
        user_id=11,
    )
    repository.latest_consent.return_value = consent(accepted=True, version="2026-07")
    service = VersionedConsentService(as_repository(repository))

    entry, created = await service.record(
        user_id=11,
        consent_type=ConsentType.PRIVACY,
        accepted=True,
        source=ConsentSource.ONBOARDING,
        policy=POLICY,
        now=NOW,
    )

    assert created
    assert entry.policy_version == "2026-08"
    repository.add_consent.assert_awaited_once_with(entry)


@pytest.mark.asyncio
async def test_repeated_refusal_for_a_new_policy_is_stored_as_new_proof() -> None:
    repository = repository_mock()
    repository.get_client_by_user.return_value = BusinessClient(
        id=5,
        business_id=7,
        user_id=11,
    )
    repository.latest_consent.return_value = consent(accepted=False, version="2026-07")
    service = VersionedConsentService(as_repository(repository))

    entry, created = await service.record(
        user_id=11,
        consent_type=ConsentType.PRIVACY,
        accepted=False,
        source=ConsentSource.ONBOARDING,
        policy=POLICY,
        now=NOW,
    )

    assert created
    assert entry.previous_value is False
    assert entry.revoked_at == NOW


@pytest.mark.asyncio
async def test_deletion_request_is_idempotent_and_does_not_duplicate_side_effects() -> None:
    repository = repository_mock()
    repository.get_client.return_value = BusinessClient(id=5, business_id=7, user_id=11)
    existing = DataDeletionRequest(
        id=23,
        business_id=7,
        business_client_id=5,
        status=DataDeletionRequestStatus.REQUESTED,
        requested_at=NOW,
    )
    repository.get_open_deletion_request.return_value = existing
    service = DataDeletionService(as_repository(repository))

    outcome = await service.request(
        business_client_id=5,
        marketing_policy=POLICY,
        correlation_id="update-101",
        now=NOW,
    )

    assert outcome.request is existing
    assert not outcome.created
    repository.add_deletion_request.assert_not_awaited()
    repository.add_deletion_event.assert_not_awaited()
    repository.add_consent.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_deletion_request_builds_safe_plan_and_revokes_marketing() -> None:
    repository = repository_mock()
    repository.get_client.return_value = BusinessClient(id=5, business_id=7, user_id=11)
    repository.get_open_deletion_request.return_value = None
    repository.latest_consent.return_value = consent(accepted=True)

    async def assign_request_id(request: DataDeletionRequest) -> DataDeletionRequest:
        request.id = 23
        return request

    repository.add_deletion_request.side_effect = assign_request_id
    service = DataDeletionService(as_repository(repository))

    outcome = await service.request(
        business_client_id=5,
        marketing_policy=POLICY,
        correlation_id="update-101",
        now=NOW,
    )

    assert outcome.created
    assert (
        "appointments.service_master_time_price_snapshots"
        in (outcome.request.anonymization_plan["retain"])
    )
    event = repository.add_deletion_event.await_args.args[0]
    assert event.new_status is DataDeletionRequestStatus.REQUESTED
    assert event.actor_user_id == 11
    revoked = repository.add_consent.await_args.args[0]
    assert revoked.consent_type is ConsentType.MARKETING
    assert revoked.new_value is False
    assert revoked.policy_version == POLICY.version


@pytest.mark.asyncio
async def test_completion_records_only_safe_result_and_requires_retention_reason() -> None:
    repository = repository_mock()
    request = DataDeletionRequest(
        id=23,
        business_id=7,
        business_client_id=5,
        status=DataDeletionRequestStatus.APPROVED,
        requested_at=NOW,
        anonymization_plan={},
        anonymization_result={},
    )
    repository.get_deletion_request.return_value = request
    service = DataDeletionService(as_repository(repository))
    result = AnonymizationResult(
        result_code="completed_with_legal_retention",
        identities_anonymized=1,
        appointment_snapshots_retained=3,
        financial_snapshots_retained=1,
    )

    with pytest.raises(PrivacyStateError):
        await service.transition(
            request_id=23,
            target=DataDeletionRequestStatus.COMPLETED,
            actor_staff_id=8,
            result=result,
            now=NOW,
        )

    completed = await service.transition(
        request_id=23,
        target=DataDeletionRequestStatus.COMPLETED,
        actor_staff_id=8,
        retention_reason="statutory_accounting_retention",
        result=result,
        now=NOW,
    )

    assert completed.status is DataDeletionRequestStatus.COMPLETED
    assert completed.processed_at == NOW
    assert completed.anonymization_result["appointment_snapshots_retained"] == 3
    event = repository.add_deletion_event.await_args.args[0]
    assert event.safe_details == {
        "result_code": "completed_with_legal_retention",
        "error_count": 0,
        "retention_reason_present": True,
    }


@pytest.mark.asyncio
async def test_acquisition_service_keeps_first_and_updates_last_touch() -> None:
    repository = repository_mock()
    repository.get_client.return_value = BusinessClient(id=5, business_id=7, user_id=11)
    repository.get_source_by_code.return_value = AcquisitionSource(
        id=20,
        business_id=7,
        code="vk",
        display_name="VK",
    )
    attribution = ClientAcquisitionAttribution(
        id=30,
        business_id=7,
        business_client_id=5,
        first_source_id=10,
        first_touched_at=NOW,
        last_source_id=10,
        last_touched_at=NOW,
        touch_count=1,
    )
    repository.get_attribution.return_value = attribution
    service = AcquisitionService(as_repository(repository))

    outcome = await service.record_touch(
        business_client_id=5,
        raw_code="VK",
        touched_at=NOW + timedelta(days=1),
    )

    assert not outcome.first_touch_created
    assert attribution.first_source_id == 10
    assert attribution.first_touched_at == NOW
    assert attribution.last_source_id == 20
    assert attribution.touch_count == 2
    repository.get_source_by_code.assert_awaited_once_with("vk")
    repository.flush.assert_awaited_once()
