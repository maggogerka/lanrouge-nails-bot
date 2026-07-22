"""Client onboarding and independent consent decisions."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from app.database.models import ConsentHistory, User
from app.domain.enums import ConsentSource, ConsentType
from app.domain.errors import PrivacyConsentRequiredError
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.booking import ClientActor, ConsentStatus, NotificationPreferences

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


class ConsentService:
    """Persist privacy and marketing consent as separate decisions."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    async def get_or_create_status(self, actor: ClientActor) -> ConsentStatus:
        async with self._unit_of_work_factory() as unit_of_work:
            user = await unit_of_work.users.get_or_create_client(actor)
            await unit_of_work.commit()
            return self._status(user)

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
            if user.privacy_consent_at is None:
                await unit_of_work.users.set_privacy_consent(user, changed_at)
                await unit_of_work.crm.add_consent_history(
                    ConsentHistory(
                        user_id=user.id,
                        consent_type=ConsentType.PRIVACY,
                        previous_value=None,
                        new_value=True,
                        source=ConsentSource.ONBOARDING,
                    )
                )
                await unit_of_work.audit.add(
                    actor_user_id=user.id,
                    action="consent.privacy_accepted",
                    entity_type="user",
                    entity_id=str(user.id),
                    changes={"accepted_at": changed_at.isoformat()},
                    correlation_id=correlation_id,
                )
            await unit_of_work.commit()
            return self._status(user)

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
            if user.privacy_consent_at is None:
                raise PrivacyConsentRequiredError(
                    "Согласие на рекламные сообщения запрашивается после основного согласия."
                )
            previous = self._marketing_value(user)
            if previous is not accepted:
                await unit_of_work.users.set_marketing_consent(
                    user,
                    accepted=accepted,
                    changed_at=changed_at,
                )
                await unit_of_work.crm.add_consent_history(
                    ConsentHistory(
                        user_id=user.id,
                        consent_type=ConsentType.MARKETING,
                        previous_value=previous,
                        new_value=accepted,
                        source=source,
                    )
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
                    },
                    correlation_id=correlation_id,
                )
            await unit_of_work.commit()
            return self._status(user)

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
            if user.privacy_consent_at is None:
                raise PrivacyConsentRequiredError(
                    "Сначала примите условия обработки данных через /start."
                )
            previous = user.repeat_booking_opt_out_at is None
            if previous is not accepted:
                user.repeat_booking_opt_out_at = None if accepted else changed_at
                await unit_of_work.crm.add_consent_history(
                    ConsentHistory(
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
    ) -> None:
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
                await unit_of_work.crm.add_consent_history(
                    ConsentHistory(
                        user_id=user.id,
                        consent_type=ConsentType.MARKETING,
                        previous_value=previous,
                        new_value=False,
                        source=ConsentSource.SYSTEM,
                    )
                )
            await unit_of_work.audit.add(
                actor_user_id=user.id,
                action="privacy.deletion_requested",
                entity_type="user",
                entity_id=str(user.id),
                changes={"requested_at": requested_at.isoformat()},
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()

    @staticmethod
    def _status(user: User) -> ConsentStatus:
        return ConsentStatus(
            privacy_accepted=user.privacy_consent_at is not None,
            marketing_answered=(
                user.marketing_consent_at is not None or user.marketing_unsubscribed_at is not None
            ),
            marketing_accepted=user.marketing_consent_at is not None,
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
