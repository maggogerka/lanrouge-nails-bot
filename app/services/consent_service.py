"""Client onboarding and independent consent decisions."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from app.database.models import User
from app.domain.errors import PrivacyConsentRequiredError
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.booking import ClientActor, ConsentStatus

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
            await unit_of_work.users.set_marketing_consent(
                user,
                accepted=accepted,
                changed_at=changed_at,
            )
            await unit_of_work.audit.add(
                actor_user_id=user.id,
                action="consent.marketing_changed",
                entity_type="user",
                entity_id=str(user.id),
                changes={"accepted": accepted, "changed_at": changed_at.isoformat()},
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return self._status(user)

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
    def _aware_now(value: datetime | None) -> datetime:
        current = value or datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("now must be timezone-aware")
        return current.astimezone(UTC)
