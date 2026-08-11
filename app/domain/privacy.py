"""Pure versioned-consent and privacy-deletion rules."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Self
from urllib.parse import urlsplit

from app.domain.enums import DataDeletionRequestStatus
from app.domain.errors import DomainError

_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SAFE_RESULT_CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{0,99}$")


class PrivacyStateError(DomainError):
    """A privacy workflow input or transition violates the domain rules."""


@dataclass(frozen=True, slots=True)
class PolicyDocument:
    """Stable identity of the exact policy text shown to a client."""

    version: str
    url: str | None
    sha256: str | None

    def __post_init__(self) -> None:
        version = self.version.strip()
        if not version or len(version) > 64:
            raise PrivacyStateError("policy version must contain 1..64 characters")
        object.__setattr__(self, "version", version)

        if self.url is None and self.sha256 is None:
            raise PrivacyStateError("policy URL or SHA-256 digest is required")
        if self.url is not None:
            url = self.url.strip()
            parsed = urlsplit(url)
            if parsed.scheme != "https" or not parsed.netloc or len(url) > 2048:
                raise PrivacyStateError("policy URL must be an absolute HTTPS URL")
            object.__setattr__(self, "url", url)
        if self.sha256 is not None:
            sha256 = self.sha256.strip().lower()
            if _SHA256_PATTERN.fullmatch(sha256) is None:
                raise PrivacyStateError("policy SHA-256 must be 64 lowercase hex characters")
            object.__setattr__(self, "sha256", sha256)

    def matches(
        self,
        *,
        version: str,
        url: str | None,
        sha256: str | None,
    ) -> bool:
        """Compare material policy identity, preferring a content digest when available."""

        if version != self.version:
            return False
        if self.sha256 is None:
            return self.url is None or url == self.url
        return sha256 is not None and sha256.lower() == self.sha256


@dataclass(frozen=True, slots=True)
class ConsentSnapshot:
    """Latest append-only consent decision reduced to domain data."""

    accepted: bool
    policy_version: str
    policy_url: str | None
    policy_hash: str | None
    decided_at: datetime
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.decided_at.tzinfo is None or self.decided_at.utcoffset() is None:
            raise PrivacyStateError("consent timestamp must be timezone-aware")
        if self.revoked_at is not None and (
            self.revoked_at.tzinfo is None or self.revoked_at.utcoffset() is None
        ):
            raise PrivacyStateError("consent revocation timestamp must be timezone-aware")


@dataclass(frozen=True, slots=True)
class ConsentAssessment:
    """Whether a current policy grants access or requires an explicit re-consent."""

    accepted: bool
    current_policy: bool
    reconsent_required: bool


def assess_consent(
    latest: ConsentSnapshot | None,
    current_policy: PolicyDocument,
) -> ConsentAssessment:
    """Assess consent without silently carrying acceptance across policy versions."""

    if latest is None or not latest.accepted:
        return ConsentAssessment(
            accepted=False,
            current_policy=False,
            reconsent_required=True,
        )
    is_current = current_policy.matches(
        version=latest.policy_version,
        url=latest.policy_url,
        sha256=latest.policy_hash,
    )
    return ConsentAssessment(
        accepted=is_current,
        current_policy=is_current,
        reconsent_required=not is_current,
    )


_DELETION_TRANSITIONS: dict[DataDeletionRequestStatus, frozenset[DataDeletionRequestStatus]] = {
    DataDeletionRequestStatus.REQUESTED: frozenset(
        {DataDeletionRequestStatus.IN_REVIEW, DataDeletionRequestStatus.CANCELLED}
    ),
    DataDeletionRequestStatus.IN_REVIEW: frozenset(
        {
            DataDeletionRequestStatus.APPROVED,
            DataDeletionRequestStatus.REJECTED,
            DataDeletionRequestStatus.CANCELLED,
        }
    ),
    DataDeletionRequestStatus.APPROVED: frozenset(
        {
            DataDeletionRequestStatus.IN_REVIEW,
            DataDeletionRequestStatus.PROCESSING,
            DataDeletionRequestStatus.FAILED,
            DataDeletionRequestStatus.COMPLETED,
        }
    ),
    DataDeletionRequestStatus.PROCESSING: frozenset(
        {
            DataDeletionRequestStatus.APPROVED,
            DataDeletionRequestStatus.COMPLETED,
            DataDeletionRequestStatus.FAILED,
        }
    ),
    DataDeletionRequestStatus.FAILED: frozenset({DataDeletionRequestStatus.APPROVED}),
    DataDeletionRequestStatus.REJECTED: frozenset(),
    DataDeletionRequestStatus.COMPLETED: frozenset(),
    DataDeletionRequestStatus.CANCELLED: frozenset(),
}


def ensure_deletion_transition(
    current: DataDeletionRequestStatus,
    target: DataDeletionRequestStatus,
) -> None:
    """Reject skips and mutations of terminal privacy-workflow states."""

    if target not in _DELETION_TRANSITIONS[current]:
        raise PrivacyStateError(f"invalid deletion transition: {current.value} -> {target.value}")


@dataclass(frozen=True, slots=True)
class AnonymizationPlan:
    """Machine-readable plan that explicitly separates anonymization and retention."""

    anonymize: tuple[str, ...]
    retain: tuple[str, ...]
    schema_version: int = 1

    @classmethod
    def standard(cls) -> Self:
        """Build the safe default: remove direct PII, preserve legal/accounting snapshots."""

        return cls(
            anonymize=(
                "user.contact_and_profile",
                "business_client.active_membership",
                "client_notes.free_text",
                "appointments.client_comments",
                "reviews.client_text",
                "reference_images.telegram_identifiers",
                "pending_client_deliveries",
                "marketing_preferences",
                "acquisition_attribution.client_link",
            ),
            retain=(
                "appointments.service_master_time_price_snapshots",
                "payments_and_refunds.accounting_snapshots",
                "audit_events.non_personal_fields",
                "consent_history.proof_fields",
            ),
        )

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "anonymize": list(self.anonymize),
            "retain": list(self.retain),
        }


@dataclass(frozen=True, slots=True)
class AnonymizationResult:
    """PII-free execution counters stored with a completed request."""

    result_code: str
    identities_anonymized: int = 0
    notes_anonymized: int = 0
    comments_anonymized: int = 0
    reviews_anonymized: int = 0
    reference_links_removed: int = 0
    deliveries_cancelled: int = 0
    consents_revoked: int = 0
    appointment_snapshots_retained: int = 0
    financial_snapshots_retained: int = 0
    error_codes: tuple[str, ...] = ()
    schema_version: int = 1

    def __post_init__(self) -> None:
        if _SAFE_RESULT_CODE_PATTERN.fullmatch(self.result_code) is None:
            raise PrivacyStateError("result code has an invalid format")
        counts = (
            self.identities_anonymized,
            self.notes_anonymized,
            self.comments_anonymized,
            self.reviews_anonymized,
            self.reference_links_removed,
            self.deliveries_cancelled,
            self.consents_revoked,
            self.appointment_snapshots_retained,
            self.financial_snapshots_retained,
        )
        if any(value < 0 for value in counts):
            raise PrivacyStateError("anonymization counters cannot be negative")
        if any(_SAFE_RESULT_CODE_PATTERN.fullmatch(code) is None for code in self.error_codes):
            raise PrivacyStateError("error codes must not contain free-form or personal data")

    def to_payload(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "result_code": self.result_code,
            "identities_anonymized": self.identities_anonymized,
            "notes_anonymized": self.notes_anonymized,
            "comments_anonymized": self.comments_anonymized,
            "reviews_anonymized": self.reviews_anonymized,
            "reference_links_removed": self.reference_links_removed,
            "deliveries_cancelled": self.deliveries_cancelled,
            "consents_revoked": self.consents_revoked,
            "appointment_snapshots_retained": self.appointment_snapshots_retained,
            "financial_snapshots_retained": self.financial_snapshots_retained,
            "error_codes": list(self.error_codes),
        }
