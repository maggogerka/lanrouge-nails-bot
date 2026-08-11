"""Application services for versioned consent and deletion-request workflows."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import uuid4

from app.database.models.crm import ConsentHistory
from app.database.models.privacy import DataDeletionRequest, DataDeletionRequestEvent
from app.domain.enums import (
    ConsentSource,
    ConsentType,
    DataDeletionRequestStatus,
    StaffRole,
)
from app.domain.errors import EntityNotFoundError
from app.domain.privacy import (
    AnonymizationPlan,
    AnonymizationResult,
    ConsentAssessment,
    ConsentSnapshot,
    PolicyDocument,
    PrivacyStateError,
    assess_consent,
    ensure_deletion_transition,
)
from app.domain.tenancy import DEFAULT_BUSINESS_ID
from app.repositories.privacy_repository import PrivacyRepository
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.authorization import StaffContext, StaffPermission
from app.services.authorization_service import AuthorizationService

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]

_LEGAL_RETENTION_REASON = "legal_accounting_and_service_history"
_ANONYMIZATION_LEASE = timedelta(minutes=15)
_ANONYMIZATION_ERROR = "anonymization_execution_failed"
_REJECTION_REASON_CODES = frozenset(
    {
        "active_staff_membership",
        "other_active_business_membership",
        "identity_not_verified",
        "legal_retention_required",
        "request_invalid",
    }
)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _snapshot(entry: ConsentHistory) -> ConsentSnapshot:
    return ConsentSnapshot(
        accepted=entry.new_value,
        policy_version=entry.policy_version,
        policy_url=entry.policy_url,
        policy_hash=entry.policy_hash,
        decided_at=entry.created_at,
        revoked_at=entry.revoked_at,
    )


class VersionedConsentService:
    """Append-only policy proof; callers own the surrounding transaction."""

    def __init__(self, repository: PrivacyRepository) -> None:
        self._repository = repository

    async def assess(
        self,
        *,
        user_id: int,
        consent_type: ConsentType,
        current_policy: PolicyDocument,
    ) -> ConsentAssessment:
        latest = await self._repository.latest_consent(user_id, consent_type)
        return assess_consent(_snapshot(latest) if latest is not None else None, current_policy)

    async def record(
        self,
        *,
        user_id: int,
        consent_type: ConsentType,
        accepted: bool,
        source: ConsentSource,
        policy: PolicyDocument,
        now: datetime | None = None,
    ) -> tuple[ConsentHistory, bool]:
        """Record a material decision once; a policy change creates a new proof row."""

        client = await self._repository.get_client_by_user(user_id, for_update=True)
        if client is None:
            raise EntityNotFoundError("business client not found")
        latest = await self._repository.latest_consent(user_id, consent_type)
        if latest is not None and self._same_decision(latest, accepted=accepted, policy=policy):
            return latest, False

        changed_at = now or _utc_now()
        entry = ConsentHistory(
            business_id=self._repository.business_id,
            user_id=user_id,
            consent_type=consent_type,
            previous_value=latest.new_value if latest is not None else None,
            new_value=accepted,
            source=source,
            policy_version=policy.version,
            policy_url=policy.url,
            policy_hash=policy.sha256,
            revoked_at=None if accepted else changed_at,
            created_at=changed_at,
        )
        await self._repository.add_consent(entry)
        return entry, True

    @staticmethod
    def _same_decision(
        latest: ConsentHistory,
        *,
        accepted: bool,
        policy: PolicyDocument,
    ) -> bool:
        if latest.new_value != accepted:
            return False
        return policy.matches(
            version=latest.policy_version,
            url=latest.policy_url,
            sha256=latest.policy_hash,
        )


@dataclass(frozen=True, slots=True)
class DeletionRequestOutcome:
    request: DataDeletionRequest
    created: bool


@dataclass(frozen=True, slots=True)
class DeletionRequestView:
    """Admin-safe deletion request projection containing no client profile data."""

    id: int
    status: DataDeletionRequestStatus
    requested_at: datetime
    result_code: str | None
    retention_reason_code: str | None
    attempt_count: int
    max_attempts: int
    last_error_code: str | None


@dataclass(frozen=True, slots=True)
class AnonymizationExecutionOutcome:
    """PII-free completion or fail-closed preflight result."""

    request: DeletionRequestView
    completed: bool
    error_codes: tuple[str, ...] = ()


class TelegramStaffActor(Protocol):
    telegram_id: int


class TelegramMessageSender(Protocol):
    async def send_message(self, chat_id: int, text: str) -> object: ...


@dataclass(frozen=True, slots=True)
class DeletionNotificationOutcome:
    """Delivery metrics safe to log or test; recipient identities are omitted."""

    eligible_count: int
    delivered_count: int
    failed_count: int
    lookup_failed: bool = False


class DeletionRequestNotificationService:
    """Best-effort PII-free notice to live owners and managers after commit."""

    def __init__(self, authorization_service: AuthorizationService) -> None:
        self._authorization_service = authorization_service

    async def notify(
        self,
        sender: TelegramMessageSender,
        *,
        business_id: int,
        request_id: int,
    ) -> DeletionNotificationOutcome:
        try:
            recipients = await self._authorization_service.list_active_staff(
                business_id=business_id,
                roles=(StaffRole.OWNER, StaffRole.MANAGER),
            )
        except Exception:
            return DeletionNotificationOutcome(0, 0, 0, lookup_failed=True)

        delivered = 0
        failed = 0
        text = (
            f"Новый запрос на удаление данных #{request_id}. "
            "Откройте раздел «Запросы на удаление» в панели администратора."
        )
        for recipient in recipients:
            try:
                await sender.send_message(recipient.telegram_id, text)
            except Exception:
                failed += 1
            else:
                delivered += 1
        return DeletionNotificationOutcome(
            eligible_count=len(recipients),
            delivered_count=delivered,
            failed_count=failed,
        )


class DataDeletionService:
    """Idempotent request creation and guarded, append-only status transitions."""

    def __init__(self, repository: PrivacyRepository) -> None:
        self._repository = repository

    async def request(
        self,
        *,
        business_client_id: int,
        marketing_policy: PolicyDocument | None = None,
        correlation_id: str | None = None,
        now: datetime | None = None,
    ) -> DeletionRequestOutcome:
        changed_at = now or _utc_now()
        client = await self._repository.get_client(business_client_id, for_update=True)
        if client is None:
            raise EntityNotFoundError("business client not found")

        existing = await self._repository.get_open_deletion_request(
            business_client_id,
            for_update=True,
        )
        if existing is not None:
            return DeletionRequestOutcome(request=existing, created=False)

        request = DataDeletionRequest(
            business_id=self._repository.business_id,
            business_client_id=business_client_id,
            status=DataDeletionRequestStatus.REQUESTED,
            correlation_id=correlation_id,
            requested_at=changed_at,
            anonymization_plan=AnonymizationPlan.standard().to_payload(),
            anonymization_result={},
        )
        await self._repository.add_deletion_request(request)
        await self._repository.add_deletion_event(
            DataDeletionRequestEvent(
                business_id=self._repository.business_id,
                request_id=request.id,
                previous_status=None,
                new_status=DataDeletionRequestStatus.REQUESTED,
                actor_user_id=client.user_id,
                actor_staff_id=None,
                safe_details={"reason_code": "client_request"},
                created_at=changed_at,
            )
        )
        await self._revoke_marketing(
            user_id=client.user_id,
            policy=marketing_policy,
            changed_at=changed_at,
        )
        return DeletionRequestOutcome(request=request, created=True)

    async def revoke_active_consents(
        self,
        *,
        user_id: int,
        now: datetime | None = None,
    ) -> int:
        """Append revocations while retaining every earlier versioned proof row."""

        changed_at = now or _utc_now()
        revoked = 0
        for consent_type in ConsentType:
            latest = await self._repository.latest_consent(user_id, consent_type)
            if latest is None or not latest.new_value:
                continue
            await self._repository.add_consent(
                ConsentHistory(
                    business_id=self._repository.business_id,
                    user_id=user_id,
                    consent_type=consent_type,
                    previous_value=True,
                    new_value=False,
                    source=ConsentSource.SYSTEM,
                    policy_version=latest.policy_version,
                    policy_url=latest.policy_url,
                    policy_hash=latest.policy_hash,
                    revoked_at=changed_at,
                    created_at=changed_at,
                )
            )
            revoked += 1
        return revoked

    async def transition(
        self,
        *,
        request_id: int,
        target: DataDeletionRequestStatus,
        actor_staff_id: int | None,
        actor_user_id: int | None = None,
        retention_reason: str | None = None,
        result: AnonymizationResult | None = None,
        lock_id: str | None = None,
        error_code: str | None = None,
        now: datetime | None = None,
    ) -> DataDeletionRequest:
        changed_at = now or _utc_now()
        request = await self._repository.get_deletion_request(request_id, for_update=True)
        if request is None:
            raise EntityNotFoundError("deletion request not found")
        previous = request.status
        ensure_deletion_transition(previous, target)
        self._validate_transition_details(
            target=target,
            actor_staff_id=actor_staff_id,
            actor_user_id=actor_user_id,
            retention_reason=retention_reason,
            result=result,
            lock_id=lock_id,
            error_code=error_code,
        )

        request.status = target
        request.processed_by_staff_id = actor_staff_id
        request.retention_reason = retention_reason
        if target is DataDeletionRequestStatus.PROCESSING:
            request.attempt_count += 1
            request.locked_at = changed_at
            request.locked_by = lock_id
            request.last_error_code = None
            request.result_code = "processing"
        elif target in {
            DataDeletionRequestStatus.APPROVED,
            DataDeletionRequestStatus.FAILED,
            DataDeletionRequestStatus.COMPLETED,
        }:
            request.locked_at = None
            request.locked_by = None
            request.last_error_code = error_code
        if target in {
            DataDeletionRequestStatus.REJECTED,
            DataDeletionRequestStatus.COMPLETED,
            DataDeletionRequestStatus.CANCELLED,
        }:
            request.processed_at = changed_at
        if result is not None:
            request.result_code = result.result_code
            request.anonymization_result = result.to_payload()

        details: dict[str, object] = {}
        if result is not None:
            details["result_code"] = result.result_code
            details["error_count"] = len(result.error_codes)
        if retention_reason is not None:
            details["retention_reason_present"] = True
        if error_code is not None:
            details["error_code"] = error_code
        if target is DataDeletionRequestStatus.PROCESSING:
            details["attempt"] = request.attempt_count
        await self._repository.add_deletion_event(
            DataDeletionRequestEvent(
                business_id=self._repository.business_id,
                request_id=request.id,
                previous_status=previous,
                new_status=target,
                actor_user_id=actor_user_id,
                actor_staff_id=actor_staff_id,
                safe_details=details,
                created_at=changed_at,
            )
        )
        await self._repository.flush()
        return request

    async def _revoke_marketing(
        self,
        *,
        user_id: int,
        policy: PolicyDocument | None,
        changed_at: datetime,
    ) -> None:
        latest = await self._repository.latest_consent(user_id, ConsentType.MARKETING)
        if latest is not None and not latest.new_value:
            return
        await self._repository.add_consent(
            ConsentHistory(
                business_id=self._repository.business_id,
                user_id=user_id,
                consent_type=ConsentType.MARKETING,
                previous_value=latest.new_value if latest is not None else None,
                new_value=False,
                source=ConsentSource.SYSTEM,
                policy_version=policy.version if policy is not None else "legacy-unversioned",
                policy_url=policy.url if policy is not None else None,
                policy_hash=policy.sha256 if policy is not None else None,
                revoked_at=changed_at,
                created_at=changed_at,
            )
        )

    @staticmethod
    def _validate_transition_details(
        *,
        target: DataDeletionRequestStatus,
        actor_staff_id: int | None,
        actor_user_id: int | None,
        retention_reason: str | None,
        result: AnonymizationResult | None,
        lock_id: str | None,
        error_code: str | None,
    ) -> None:
        staff_states = {
            DataDeletionRequestStatus.IN_REVIEW,
            DataDeletionRequestStatus.APPROVED,
            DataDeletionRequestStatus.REJECTED,
            DataDeletionRequestStatus.COMPLETED,
            DataDeletionRequestStatus.PROCESSING,
            DataDeletionRequestStatus.FAILED,
        }
        if target in staff_states and actor_staff_id is None:
            raise PrivacyStateError("this transition requires a staff actor")
        if target is DataDeletionRequestStatus.CANCELLED:
            has_single_actor = (actor_staff_id is None) != (actor_user_id is None)
            if not has_single_actor:
                raise PrivacyStateError("cancellation requires exactly one actor")
        if target is DataDeletionRequestStatus.REJECTED and not retention_reason:
            raise PrivacyStateError("rejection requires a retention/rejection reason")
        if target is DataDeletionRequestStatus.COMPLETED:
            if result is None:
                raise PrivacyStateError("completion requires an anonymization result")
            retained = result.appointment_snapshots_retained + result.financial_snapshots_retained
            if retained and not retention_reason:
                raise PrivacyStateError("retained snapshots require a legal retention reason")
        elif result is not None:
            raise PrivacyStateError("anonymization result is only valid on completion")
        if target is DataDeletionRequestStatus.PROCESSING and not lock_id:
            raise PrivacyStateError("processing requires a worker lock")
        if target is not DataDeletionRequestStatus.PROCESSING and lock_id is not None:
            raise PrivacyStateError("worker lock is only valid while processing")
        if target is DataDeletionRequestStatus.FAILED and not error_code:
            raise PrivacyStateError("failure requires a safe error code")
        if target is not DataDeletionRequestStatus.FAILED and error_code is not None:
            raise PrivacyStateError("error code is only valid on failure")


class PrivacyDeletionRuntimeService:
    """Live-authorized, tenant-scoped administration and anonymization workflow."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        authorization_service: AuthorizationService,
        *,
        business_id: int = DEFAULT_BUSINESS_ID,
    ) -> None:
        if business_id <= 0:
            raise ValueError("business_id must be positive")
        self._unit_of_work_factory = unit_of_work_factory
        self._authorization_service = authorization_service
        self._business_id = business_id

    async def list_requests(
        self,
        actor: TelegramStaffActor,
        *,
        statuses: tuple[DataDeletionRequestStatus, ...] | None = None,
        limit: int = 50,
    ) -> tuple[DeletionRequestView, ...]:
        await self._authorize(actor)
        async with self._unit_of_work_factory() as unit_of_work:
            self._require_uow_scope(unit_of_work)
            requests = await unit_of_work.privacy.list_deletion_requests(
                statuses=statuses,
                limit=limit,
            )
            return tuple(self._view(request) for request in requests)

    async def get_request(
        self,
        actor: TelegramStaffActor,
        request_id: int,
    ) -> DeletionRequestView:
        await self._authorize(actor)
        async with self._unit_of_work_factory() as unit_of_work:
            self._require_uow_scope(unit_of_work)
            request = await unit_of_work.privacy.get_deletion_request(request_id)
            if request is None:
                raise EntityNotFoundError("deletion request not found")
            return self._view(request)

    async def start_review(
        self,
        actor: TelegramStaffActor,
        request_id: int,
        *,
        confirmed: bool,
        correlation_id: str | None = None,
        now: datetime | None = None,
    ) -> DeletionRequestView:
        self._require_confirmation(confirmed)
        return await self._transition(
            actor,
            request_id,
            DataDeletionRequestStatus.IN_REVIEW,
            correlation_id=correlation_id,
            now=now,
        )

    async def approve(
        self,
        actor: TelegramStaffActor,
        request_id: int,
        *,
        confirmed: bool,
        correlation_id: str | None = None,
        now: datetime | None = None,
    ) -> DeletionRequestView:
        self._require_confirmation(confirmed)
        return await self._transition(
            actor,
            request_id,
            DataDeletionRequestStatus.APPROVED,
            correlation_id=correlation_id,
            now=now,
        )

    async def reject(
        self,
        actor: TelegramStaffActor,
        request_id: int,
        *,
        reason_code: str,
        confirmed: bool,
        correlation_id: str | None = None,
        now: datetime | None = None,
    ) -> DeletionRequestView:
        self._require_confirmation(confirmed)
        if reason_code not in _REJECTION_REASON_CODES:
            raise PrivacyStateError("a predefined rejection reason is required")
        return await self._transition(
            actor,
            request_id,
            DataDeletionRequestStatus.REJECTED,
            retention_reason=reason_code,
            correlation_id=correlation_id,
            now=now,
        )

    async def execute_anonymization(
        self,
        actor: TelegramStaffActor,
        request_id: int,
        *,
        confirmed: bool,
        correlation_id: str | None = None,
        now: datetime | None = None,
    ) -> AnonymizationExecutionOutcome:
        self._require_confirmation(confirmed)
        live_actor = await self._authorize(actor)
        changed_at = now or _utc_now()
        lock_id = f"privacy-{uuid4().hex}"

        claim = await self._claim_anonymization(
            live_actor,
            request_id,
            lock_id=lock_id,
            correlation_id=correlation_id,
            now=changed_at,
        )
        if claim is not None:
            return claim

        try:
            return await self._run_claimed_anonymization(
                live_actor,
                request_id,
                lock_id=lock_id,
                correlation_id=correlation_id,
                now=changed_at,
            )
        except Exception:
            return await self._record_execution_failure(
                live_actor,
                request_id,
                lock_id=lock_id,
                correlation_id=correlation_id,
                now=changed_at,
            )

    async def retry_failed(
        self,
        actor: TelegramStaffActor,
        request_id: int,
        *,
        confirmed: bool,
        correlation_id: str | None = None,
        now: datetime | None = None,
    ) -> DeletionRequestView:
        """Allow only the immutable bootstrap owner to schedule one bounded retry."""

        self._require_confirmation(confirmed)
        live_actor = await self._authorize(actor)
        if not live_actor.is_bootstrap_owner:
            raise PrivacyStateError("only the bootstrap owner can retry a failed request")
        changed_at = now or _utc_now()
        async with self._unit_of_work_factory() as unit_of_work:
            self._require_uow_scope(unit_of_work)
            request = await unit_of_work.privacy.get_deletion_request(
                request_id,
                for_update=True,
            )
            if request is None:
                raise EntityNotFoundError("deletion request not found")
            if request.status is DataDeletionRequestStatus.COMPLETED:
                return self._view(request)
            if request.status is not DataDeletionRequestStatus.FAILED:
                raise PrivacyStateError("only a failed request can be retried")
            request.attempt_count = 0
            request.last_error_code = None
            request.result_code = "retry_approved"
            request = await DataDeletionService(unit_of_work.privacy).transition(
                request_id=request.id,
                target=DataDeletionRequestStatus.APPROVED,
                actor_staff_id=live_actor.staff_member_id,
                now=changed_at,
            )
            await unit_of_work.audit.add(
                actor_user_id=live_actor.user_id,
                action="privacy.deletion_retry_approved",
                entity_type="data_deletion_request",
                entity_id=str(request.id),
                changes={"attempt_count_reset": True},
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return self._view(request)

    async def _claim_anonymization(
        self,
        live_actor: StaffContext,
        request_id: int,
        *,
        lock_id: str,
        correlation_id: str | None,
        now: datetime,
    ) -> AnonymizationExecutionOutcome | None:
        async with self._unit_of_work_factory() as unit_of_work:
            self._require_uow_scope(unit_of_work)
            request = await unit_of_work.privacy.get_deletion_request(request_id, for_update=True)
            if request is None:
                raise EntityNotFoundError("deletion request not found")
            request.attempt_count = request.attempt_count or 0
            request.max_attempts = request.max_attempts or 3
            if request.status is DataDeletionRequestStatus.COMPLETED:
                return AnonymizationExecutionOutcome(request=self._view(request), completed=True)
            if request.status is DataDeletionRequestStatus.PROCESSING:
                lease_is_fresh = (
                    request.locked_at is not None and request.locked_at > now - _ANONYMIZATION_LEASE
                )
                if lease_is_fresh:
                    return AnonymizationExecutionOutcome(
                        request=self._view(request),
                        completed=False,
                        error_codes=("anonymization_in_progress",),
                    )
                await DataDeletionService(unit_of_work.privacy).transition(
                    request_id=request.id,
                    target=DataDeletionRequestStatus.FAILED,
                    actor_staff_id=live_actor.staff_member_id,
                    error_code="anonymization_lease_expired",
                    now=now,
                )
                if request.attempt_count >= request.max_attempts:
                    await unit_of_work.commit()
                    return AnonymizationExecutionOutcome(
                        request=self._view(request),
                        completed=False,
                        error_codes=("anonymization_attempts_exhausted",),
                    )
                await DataDeletionService(unit_of_work.privacy).transition(
                    request_id=request.id,
                    target=DataDeletionRequestStatus.APPROVED,
                    actor_staff_id=live_actor.staff_member_id,
                    now=now,
                )
            if request.status is DataDeletionRequestStatus.FAILED:
                return AnonymizationExecutionOutcome(
                    request=self._view(request),
                    completed=False,
                    error_codes=(request.last_error_code or _ANONYMIZATION_ERROR,),
                )
            if request.status is not DataDeletionRequestStatus.APPROVED:
                raise PrivacyStateError("only an approved request can be executed")
            client = await unit_of_work.privacy.get_client(
                request.business_client_id,
                for_update=True,
            )
            if client is None:
                raise EntityNotFoundError("business client not found")
            user = await unit_of_work.privacy.get_user(client.user_id, for_update=True)
            if user is None:
                raise EntityNotFoundError("privacy subject not found")

            blockers = await unit_of_work.privacy.anonymization_blockers(user.id)
            if blockers.error_codes:
                blocked_reason = blockers.error_codes[0]
                request = await DataDeletionService(unit_of_work.privacy).transition(
                    request_id=request.id,
                    target=DataDeletionRequestStatus.IN_REVIEW,
                    actor_staff_id=live_actor.staff_member_id,
                    retention_reason=blocked_reason,
                    now=now,
                )
                await unit_of_work.audit.add(
                    actor_user_id=live_actor.user_id,
                    action="privacy.deletion_execution_blocked",
                    entity_type="data_deletion_request",
                    entity_id=str(request.id),
                    changes={"error_codes": list(blockers.error_codes)},
                    correlation_id=correlation_id,
                )
                await unit_of_work.commit()
                return AnonymizationExecutionOutcome(
                    request=self._view(request),
                    completed=False,
                    error_codes=blockers.error_codes,
                )

            if request.attempt_count >= request.max_attempts:
                request = await DataDeletionService(unit_of_work.privacy).transition(
                    request_id=request.id,
                    target=DataDeletionRequestStatus.FAILED,
                    actor_staff_id=live_actor.staff_member_id,
                    error_code="anonymization_attempts_exhausted",
                    now=now,
                )
                await unit_of_work.commit()
                return AnonymizationExecutionOutcome(
                    request=self._view(request),
                    completed=False,
                    error_codes=("anonymization_attempts_exhausted",),
                )
            request = await DataDeletionService(unit_of_work.privacy).transition(
                request_id=request.id,
                target=DataDeletionRequestStatus.PROCESSING,
                actor_staff_id=live_actor.staff_member_id,
                lock_id=lock_id,
                now=now,
            )
            await unit_of_work.audit.add(
                actor_user_id=live_actor.user_id,
                action="privacy.deletion_processing",
                entity_type="data_deletion_request",
                entity_id=str(request.id),
                changes={"attempt": request.attempt_count},
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return None

    async def _run_claimed_anonymization(
        self,
        live_actor: StaffContext,
        request_id: int,
        *,
        lock_id: str,
        correlation_id: str | None,
        now: datetime,
    ) -> AnonymizationExecutionOutcome:
        async with self._unit_of_work_factory() as unit_of_work:
            self._require_uow_scope(unit_of_work)
            request = await unit_of_work.privacy.get_deletion_request(request_id, for_update=True)
            if request is None:
                raise EntityNotFoundError("deletion request not found")
            if request.status is DataDeletionRequestStatus.COMPLETED:
                return AnonymizationExecutionOutcome(request=self._view(request), completed=True)
            if (
                request.status is not DataDeletionRequestStatus.PROCESSING
                or request.locked_by != lock_id
            ):
                raise PrivacyStateError("anonymization claim is no longer owned")
            client = await unit_of_work.privacy.get_client(
                request.business_client_id,
                for_update=True,
            )
            if client is None:
                raise EntityNotFoundError("business client not found")
            user = await unit_of_work.privacy.get_user(client.user_id, for_update=True)
            if user is None:
                raise EntityNotFoundError("privacy subject not found")

            deletion = DataDeletionService(unit_of_work.privacy)
            consents_revoked = await deletion.revoke_active_consents(
                user_id=user.id,
                now=now,
            )
            counts = await unit_of_work.privacy.anonymize_client_data(
                business_client_id=client.id,
                user_id=user.id,
                changed_at=now,
            )
            result = AnonymizationResult(
                result_code="completed_with_legal_retention",
                identities_anonymized=counts.identities_anonymized,
                notes_anonymized=counts.notes_anonymized,
                comments_anonymized=counts.comments_anonymized,
                reviews_anonymized=counts.reviews_anonymized,
                reference_links_removed=counts.reference_links_removed,
                deliveries_cancelled=counts.deliveries_cancelled,
                consents_revoked=consents_revoked,
                appointment_snapshots_retained=counts.appointment_snapshots_retained,
                financial_snapshots_retained=counts.financial_snapshots_retained,
            )
            request = await deletion.transition(
                request_id=request.id,
                target=DataDeletionRequestStatus.COMPLETED,
                actor_staff_id=live_actor.staff_member_id,
                retention_reason=_LEGAL_RETENTION_REASON,
                result=result,
                now=now,
            )
            await unit_of_work.audit.add(
                actor_user_id=live_actor.user_id,
                action="privacy.deletion_completed",
                entity_type="data_deletion_request",
                entity_id=str(request.id),
                changes=result.to_payload(),
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return AnonymizationExecutionOutcome(
                request=self._view(request),
                completed=True,
            )

    async def _record_execution_failure(
        self,
        live_actor: StaffContext,
        request_id: int,
        *,
        lock_id: str,
        correlation_id: str | None,
        now: datetime,
    ) -> AnonymizationExecutionOutcome:
        async with self._unit_of_work_factory() as unit_of_work:
            self._require_uow_scope(unit_of_work)
            request = await unit_of_work.privacy.get_deletion_request(request_id, for_update=True)
            if request is None:
                raise EntityNotFoundError("deletion request not found")
            if request.status is DataDeletionRequestStatus.COMPLETED:
                return AnonymizationExecutionOutcome(request=self._view(request), completed=True)
            if (
                request.status is DataDeletionRequestStatus.PROCESSING
                and request.locked_by == lock_id
            ):
                exhausted = request.attempt_count >= request.max_attempts
                request = await DataDeletionService(unit_of_work.privacy).transition(
                    request_id=request.id,
                    target=(
                        DataDeletionRequestStatus.FAILED
                        if exhausted
                        else DataDeletionRequestStatus.APPROVED
                    ),
                    actor_staff_id=live_actor.staff_member_id,
                    error_code=_ANONYMIZATION_ERROR if exhausted else None,
                    now=now,
                )
                request.last_error_code = _ANONYMIZATION_ERROR
                request.result_code = "failed" if exhausted else "retry_scheduled"
                await unit_of_work.audit.add(
                    actor_user_id=live_actor.user_id,
                    action="privacy.deletion_failed",
                    entity_type="data_deletion_request",
                    entity_id=str(request.id),
                    changes={
                        "error_code": _ANONYMIZATION_ERROR,
                        "attempt": request.attempt_count,
                        "attempts_exhausted": exhausted,
                    },
                    correlation_id=correlation_id,
                )
                await unit_of_work.commit()
            return AnonymizationExecutionOutcome(
                request=self._view(request),
                completed=False,
                error_codes=(request.last_error_code or _ANONYMIZATION_ERROR,),
            )

    async def _transition(
        self,
        actor: TelegramStaffActor,
        request_id: int,
        target: DataDeletionRequestStatus,
        *,
        retention_reason: str | None = None,
        correlation_id: str | None = None,
        now: datetime | None = None,
    ) -> DeletionRequestView:
        live_actor = await self._authorize(actor)
        async with self._unit_of_work_factory() as unit_of_work:
            self._require_uow_scope(unit_of_work)
            request = await DataDeletionService(unit_of_work.privacy).transition(
                request_id=request_id,
                target=target,
                actor_staff_id=live_actor.staff_member_id,
                retention_reason=retention_reason,
                now=now,
            )
            await unit_of_work.audit.add(
                actor_user_id=live_actor.user_id,
                action=f"privacy.deletion_{target.value}",
                entity_type="data_deletion_request",
                entity_id=str(request.id),
                changes={
                    "status": target.value,
                    "reason_code": retention_reason,
                },
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return self._view(request)

    async def _authorize(self, actor: TelegramStaffActor) -> StaffContext:
        return await self._authorization_service.authorize(
            business_id=self._business_id,
            telegram_id=actor.telegram_id,
            permission=StaffPermission.HANDLE_DATA_DELETION,
        )

    def _require_uow_scope(self, unit_of_work: SqlAlchemyUnitOfWork) -> None:
        if unit_of_work.business_id != self._business_id:
            raise RuntimeError("privacy unit of work tenant mismatch")

    @staticmethod
    def _require_confirmation(confirmed: bool) -> None:
        if not confirmed:
            raise PrivacyStateError("explicit confirmation is required")

    @staticmethod
    def _view(request: DataDeletionRequest) -> DeletionRequestView:
        return DeletionRequestView(
            id=request.id,
            status=request.status,
            requested_at=request.requested_at,
            result_code=request.result_code,
            retention_reason_code=request.retention_reason,
            attempt_count=request.attempt_count or 0,
            max_attempts=request.max_attempts or 3,
            last_error_code=request.last_error_code,
        )
