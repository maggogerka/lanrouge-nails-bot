"""Pure-domain tests for versioned consent and safe anonymization artifacts."""

from datetime import UTC, datetime

import pytest

from app.domain.enums import DataDeletionRequestStatus
from app.domain.privacy import (
    AnonymizationPlan,
    AnonymizationResult,
    ConsentSnapshot,
    PolicyDocument,
    PrivacyStateError,
    assess_consent,
    ensure_deletion_transition,
)

NOW = datetime(2026, 8, 10, 9, tzinfo=UTC)
HASH_V1 = "a" * 64
HASH_V2 = "b" * 64


def policy(version: str = "2026-08", digest: str = HASH_V1) -> PolicyDocument:
    return PolicyDocument(
        version=version,
        url=f"https://example.test/privacy/{version}",
        sha256=digest,
    )


def test_current_policy_acceptance_is_valid() -> None:
    latest = ConsentSnapshot(
        accepted=True,
        policy_version="2026-08",
        policy_url="https://example.test/privacy/2026-08",
        policy_hash=HASH_V1,
        decided_at=NOW,
    )

    assessment = assess_consent(latest, policy())

    assert assessment.accepted
    assert assessment.current_policy
    assert not assessment.reconsent_required


@pytest.mark.parametrize(
    ("latest", "current"),
    [
        (None, policy()),
        (
            ConsentSnapshot(False, "2026-08", None, HASH_V1, NOW, revoked_at=NOW),
            policy(),
        ),
        (ConsentSnapshot(True, "2026-07", None, HASH_V1, NOW), policy()),
        (ConsentSnapshot(True, "2026-08", None, HASH_V1, NOW), policy(digest=HASH_V2)),
    ],
)
def test_missing_revoked_or_changed_policy_requires_reconsent(
    latest: ConsentSnapshot | None,
    current: PolicyDocument,
) -> None:
    assessment = assess_consent(latest, current)

    assert not assessment.accepted
    assert assessment.reconsent_required


def test_url_change_requires_reconsent_when_no_content_hash_is_available() -> None:
    latest = ConsentSnapshot(
        accepted=True,
        policy_version="2026-08",
        policy_url="https://example.test/privacy/old",
        policy_hash=None,
        decided_at=NOW,
    )
    current = PolicyDocument(
        version="2026-08",
        url="https://example.test/privacy/new",
        sha256=None,
    )

    assert assess_consent(latest, current).reconsent_required


@pytest.mark.parametrize(
    "kwargs",
    [
        {"version": "", "url": "https://example.test/p", "sha256": HASH_V1},
        {"version": "v1", "url": "http://example.test/p", "sha256": HASH_V1},
        {"version": "v1", "url": None, "sha256": "bad"},
        {"version": "v1", "url": None, "sha256": None},
    ],
)
def test_policy_document_rejects_unverifiable_identity(kwargs: dict[str, str | None]) -> None:
    with pytest.raises(PrivacyStateError):
        PolicyDocument(**kwargs)  # type: ignore[arg-type]


def test_deletion_workflow_forbids_skips_and_terminal_mutation() -> None:
    ensure_deletion_transition(
        DataDeletionRequestStatus.REQUESTED,
        DataDeletionRequestStatus.IN_REVIEW,
    )
    ensure_deletion_transition(
        DataDeletionRequestStatus.IN_REVIEW,
        DataDeletionRequestStatus.APPROVED,
    )
    ensure_deletion_transition(
        DataDeletionRequestStatus.APPROVED,
        DataDeletionRequestStatus.COMPLETED,
    )

    with pytest.raises(PrivacyStateError):
        ensure_deletion_transition(
            DataDeletionRequestStatus.REQUESTED,
            DataDeletionRequestStatus.COMPLETED,
        )
    with pytest.raises(PrivacyStateError):
        ensure_deletion_transition(
            DataDeletionRequestStatus.COMPLETED,
            DataDeletionRequestStatus.IN_REVIEW,
        )


def test_standard_plan_preserves_financial_and_appointment_snapshots() -> None:
    payload = AnonymizationPlan.standard().to_payload()

    assert "appointments.service_master_time_price_snapshots" in payload["retain"]
    assert "payments_and_refunds.accounting_snapshots" in payload["retain"]
    assert "user.contact_and_profile" in payload["anonymize"]


def test_anonymization_result_is_pii_free_and_bounded() -> None:
    result = AnonymizationResult(
        result_code="completed_with_legal_retention",
        identities_anonymized=1,
        appointment_snapshots_retained=2,
        financial_snapshots_retained=1,
    )

    assert result.to_payload()["financial_snapshots_retained"] == 1
    with pytest.raises(PrivacyStateError):
        AnonymizationResult(result_code="Client Jane Doe was removed")
    with pytest.raises(PrivacyStateError):
        AnonymizationResult(result_code="completed", identities_anonymized=-1)
