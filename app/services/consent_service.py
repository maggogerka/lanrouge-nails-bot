"""Client onboarding and independent consent decisions."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from app.database.models import ConsentHistory, User
from app.domain.enums import ConsentSource, ConsentType
from app.domain.errors import PrivacyConsentRequiredError
from app.domain.legal import marketing_consent_policy
from app.domain.privacy import PolicyDocument, PrivacyStateError
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.booking import ClientActor, ConsentStatus, NotificationPreferences
from app.services.privacy_service import (
    DataDeletionService,
    DeletionRequestOutcome,
    VersionedConsentService,
)

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


class ConsentService:
    """Persist privacy and marketing consent as separate decisions."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        *,
        fallback_privacy_policy_url: str | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._fallback_privacy_policy_url = fallback_privacy_policy_url

    async def get_or_create_status(self, actor: ClientActor) -> ConsentStatus:
        async with self._unit_of_work_factory() as unit_of_work:
            user = await unit_of_work.users.get_or_create_client(actor)
            status = await self._status_with_current_policy(unit_of_work, user)
            await unit_of_work.commit()
            return status

    async def accept_privacy(
        self,
        actor: ClientActor,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ConsentStatus:
        changed_at = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            user = await unit_of_work.users.get_or_create_client(actor)
            policy = await self._privacy_policy(unit_of_work)
            if policy is None:
                raise PrivacyConsentRequiredError(
                    "Политика конфиденциальности не настроена владельцем бизнеса."
                )
            current = await self._is_current_decision(
                unit_of_work,
                user_id=user.id,
                consent_type=ConsentType.PRIVACY,
                policy=policy,
                accepted=True,
            )
            if not current:
                await unit_of_work.users.set_privacy_consent(user, changed_at)
                await VersionedConsentService(unit_of_work.privacy).record(
                    user_id=user.id,
                    consent_type=ConsentType.PRIVACY,
                    accepted=True,
                    source=ConsentSource.ONBOARDING,
                    policy=policy,
                    now=changed_at,
                )
                await unit_of_work.audit.add(
                    actor_user_id=user.id,
                    action="consent.privacy_accepted",
                    entity_type="user",
                    entity_id=str(user.id),
                    changes={
                        "accepted_at": changed_at.isoformat(),
                        "policy_version": policy.version,
                        "policy_hash_present": policy.sha256 is not None,
                    },
                    correlation_id=correlation_id,
                )
            await unit_of_work.commit()
            return await self._status_with_current_policy(unit_of_work, user)

    async def set_marketing(
        self,
        actor: ClientActor,
        *,
        accepted: bool,
        source: ConsentSource = ConsentSource.ONBOARDING,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> ConsentStatus:
        changed_at = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            user = await unit_of_work.users.get_or_create_client(actor)
            privacy_policy = await self._privacy_policy(unit_of_work)
            privacy_accepted = await self._is_current_decision(
                unit_of_work,
                user_id=user.id,
                consent_type=ConsentType.PRIVACY,
                policy=privacy_policy,
                accepted=True,
            )
            if not privacy_accepted:
                raise PrivacyConsentRequiredError(
                    "Согласие на рассылку запрашивается после актуального согласия "
                    "на обработку персональных данных."
                )
            previous = self._marketing_value(user)
            policy = marketing_consent_policy()
            current = await self._is_current_decision(
                unit_of_work,
                user_id=user.id,
                consent_type=ConsentType.MARKETING,
                policy=policy,
                accepted=accepted,
            )
            if previous is not accepted or not current:
                await unit_of_work.users.set_marketing_consent(
                    user,
                    accepted=accepted,
                    changed_at=changed_at,
                )
                await VersionedConsentService(unit_of_work.privacy).record(
                    user_id=user.id,
                    consent_type=ConsentType.MARKETING,
                    accepted=accepted,
                    source=source,
                    policy=policy,
                    now=changed_at,
                )
                await unit_of_work.audit.add(
                    actor_user_id=user.id,
                    action="consent.marketing_changed",
                    entity_type="user",
                    entity_id=str(user.id),
                    changes={
                        "accepted": accepted,
                        "source": source.value,
                        "changed_at": changed_at.isoformat(),
                        "policy_version": policy.version,
                        "policy_hash_present": True,
                    },
                    correlation_id=correlation_id,
                )
            await unit_of_work.commit()
            return await self._status_with_current_policy(unit_of_work, user)

    async def get_notification_preferences(self, actor: ClientActor) -> NotificationPreferences:
        async with self._unit_of_work_factory() as unit_of_work:
            user = await unit_of_work.users.get_or_create_client(actor)
            await unit_of_work.commit()
            return self._preferences(user)

    async def set_repeat_booking(
        self,
        actor: ClientActor,
        *,
        accepted: bool,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> NotificationPreferences:
        changed_at = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            user = await unit_of_work.users.get_or_create_client(actor)
            if not await self._is_current_decision(
                unit_of_work,
                user_id=user.id,
                consent_type=ConsentType.PRIVACY,
                policy=await self._privacy_policy(unit_of_work),
                accepted=True,
            ):
                raise PrivacyConsentRequiredError(
                    "Сначала примите актуальные условия обработки данных через /start."
                )
            previous = user.repeat_booking_opt_out_at is None
            if previous is not accepted:
                user.repeat_booking_opt_out_at = None if accepted else changed_at
                await unit_of_work.crm.add_consent_history(
                    ConsentHistory(
                        business_id=unit_of_work.business_id,
                        user_id=user.id,
                        consent_type=ConsentType.REPEAT_BOOKING,
                        previous_value=previous,
                        new_value=accepted,
                        source=ConsentSource.NOTIFICATION_SETTINGS,
                    )
                )
                await unit_of_work.audit.add(
                    actor_user_id=user.id,
                    action="consent.repeat_booking_changed",
                    entity_type="user",
                    entity_id=str(user.id),
                    changes={
                        "accepted": accepted,
                        "source": ConsentSource.NOTIFICATION_SETTINGS.value,
                        "changed_at": changed_at.isoformat(),
                    },
                    correlation_id=correlation_id,
                )
            await unit_of_work.commit()
            return self._preferences(user)

    async def request_deletion(
        self,
        actor: ClientActor,
        *,
        now: datetime | None = None,
        correlation_id: str | None = None,
    ) -> DeletionRequestOutcome:
        """Record a reviewable request without destroying required booking history."""

        requested_at = self._aware_now(now)
        async with self._unit_of_work_factory() as unit_of_work:
            user = await unit_of_work.users.get_or_create_client(actor)
            previous = self._marketing_value(user)
            if previous is not False:
                await unit_of_work.users.set_marketing_consent(
                    user,
                    accepted=False,
                    changed_at=requested_at,
                )
            await unit_of_work.audit.add(
                actor_user_id=user.id,
                action="privacy.deletion_requested",
                entity_type="user",
                entity_id=str(user.id),
                changes={"requested_at": requested_at.isoformat()},
                correlation_id=correlation_id,
            )
            business_client = await unit_of_work.privacy.get_client_by_user(
                user.id, for_update=True
            )
            if business_client is None:
                raise RuntimeError("Business client membership is missing")
            outcome = await DataDeletionService(unit_of_work.privacy).request(
                business_client_id=business_client.id,
                marketing_policy=marketing_consent_policy(),
                correlation_id=correlation_id,
                now=requested_at,
            )
            await unit_of_work.audit.add(
                actor_user_id=user.id,
                action="privacy.deletion_request_persisted",
                entity_type="data_deletion_request",
                entity_id=str(outcome.request.id),
                changes={"created": outcome.created},
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return outcome

    async def _status_with_current_policy(
        self,
        unit_of_work: SqlAlchemyUnitOfWork,
        user: User,
    ) -> ConsentStatus:
        privacy_accepted = await self._is_current_decision(
            unit_of_work,
            user_id=user.id,
            consent_type=ConsentType.PRIVACY,
            policy=await self._privacy_policy(unit_of_work),
            accepted=True,
        )
        marketing_policy = marketing_consent_policy()
        latest_marketing = await unit_of_work.privacy.latest_consent(
            user.id,
            ConsentType.MARKETING,
        )
        marketing_answered = self._matches_policy(latest_marketing, marketing_policy)
        return ConsentStatus(
            privacy_accepted=privacy_accepted,
            marketing_answered=marketing_answered,
            marketing_accepted=bool(
                marketing_answered and latest_marketing is not None and latest_marketing.new_value
            ),
        )

    async def _privacy_policy(
        self,
        unit_of_work: SqlAlchemyUnitOfWork,
    ) -> PolicyDocument | None:
        business = await unit_of_work.privacy.get_business()
        if business is None:
            return None
        url = business.privacy_policy_url or self._fallback_privacy_policy_url
        digest = business.privacy_policy_hash
        if url is None and digest is None:
            return None
        version = business.privacy_policy_version or "published-v1"
        try:
            return PolicyDocument(version=version, url=url, sha256=digest)
        except PrivacyStateError:
            return None

    @classmethod
    async def _is_current_decision(
        cls,
        unit_of_work: SqlAlchemyUnitOfWork,
        *,
        user_id: int,
        consent_type: ConsentType,
        policy: PolicyDocument | None,
        accepted: bool,
    ) -> bool:
        if policy is None:
            return False
        latest = await unit_of_work.privacy.latest_consent(user_id, consent_type)
        return (
            cls._matches_policy(latest, policy)
            and latest is not None
            and latest.new_value is accepted
        )

    @staticmethod
    def _matches_policy(entry: ConsentHistory | None, policy: PolicyDocument) -> bool:
        if entry is None:
            return False
        return policy.matches(
            version=entry.policy_version,
            url=entry.policy_url,
            sha256=entry.policy_hash,
        )

    @staticmethod
    def _preferences(user: User) -> NotificationPreferences:
        return NotificationPreferences(
            marketing_enabled=user.marketing_consent_at is not None,
            repeat_booking_enabled=user.repeat_booking_opt_out_at is None,
        )

    @staticmethod
    def _marketing_value(user: User) -> bool | None:
        if user.marketing_consent_at is not None:
            return True
        if user.marketing_unsubscribed_at is not None:
            return False
        return None

    @staticmethod
    def _aware_now(value: datetime | None) -> datetime:
        current = value or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return current.astimezone(UTC)
