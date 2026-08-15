"""Application service for safe deep-link source attribution."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.database.models.privacy import AcquisitionSource, ClientAcquisitionAttribution
from app.domain.acquisition import (
    AttributionProjection,
    CampaignValidationError,
    validate_campaign_code,
)
from app.domain.enums import ConsentType
from app.domain.errors import EntityNotFoundError
from app.domain.privacy import PolicyDocument, PrivacyStateError
from app.repositories.privacy_repository import PrivacyRepository
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.booking import ClientActor


@dataclass(frozen=True, slots=True)
class AttributionOutcome:
    attribution: ClientAcquisitionAttribution
    first_touch_created: bool


class AcquisitionService:
    """Project validated campaign touches without overwriting the first source."""

    def __init__(self, repository: PrivacyRepository) -> None:
        self._repository = repository

    async def create_source(
        self,
        *,
        raw_code: str,
        display_name: str,
        channel: str | None = None,
        actor_staff_id: int | None = None,
    ) -> AcquisitionSource:
        code = validate_campaign_code(raw_code)
        normalized_name = display_name.strip()
        if not normalized_name or len(normalized_name) > 255:
            raise ValueError("display_name must contain 1..255 characters")
        existing = await self._repository.get_source_by_code(code, active_only=False)
        if existing is not None:
            return existing
        return await self._repository.add_source(
            AcquisitionSource(
                business_id=self._repository.business_id,
                code=code,
                display_name=normalized_name,
                channel=channel,
                created_by_staff_id=actor_staff_id,
            )
        )

    async def record_touch(
        self,
        *,
        business_client_id: int,
        raw_code: str,
        touched_at: datetime | None = None,
    ) -> AttributionOutcome:
        code = validate_campaign_code(raw_code)
        client = await self._repository.get_client(business_client_id, for_update=True)
        if client is None:
            raise EntityNotFoundError("business client not found")
        source = await self._repository.get_source_by_code(code)
        if source is None:
            raise EntityNotFoundError("active acquisition source not found")

        happened_at = touched_at or datetime.now(UTC)
        attribution = await self._repository.get_attribution(
            business_client_id,
            for_update=True,
        )
        if attribution is None:
            projection = AttributionProjection.first(
                source_id=source.id,
                touched_at=happened_at,
            )
            attribution = ClientAcquisitionAttribution(
                business_id=self._repository.business_id,
                business_client_id=business_client_id,
                first_source_id=projection.first_source_id,
                first_touched_at=projection.first_touched_at,
                last_source_id=projection.last_source_id,
                last_touched_at=projection.last_touched_at,
                touch_count=projection.touch_count,
            )
            await self._repository.add_attribution(attribution)
            return AttributionOutcome(attribution=attribution, first_touch_created=True)

        projection = AttributionProjection(
            first_source_id=attribution.first_source_id,
            first_touched_at=attribution.first_touched_at,
            last_source_id=attribution.last_source_id,
            last_touched_at=attribution.last_touched_at,
            touch_count=attribution.touch_count,
        ).touch(source_id=source.id, touched_at=happened_at)
        attribution.last_source_id = projection.last_source_id
        attribution.last_touched_at = projection.last_touched_at
        attribution.touch_count = projection.touch_count
        await self._repository.flush()
        return AttributionOutcome(attribution=attribution, first_touch_created=False)


UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


class AcquisitionRuntimeService:
    """Persist a known campaign only after the caller has current privacy consent."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        *,
        fallback_privacy_policy_url: str | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._fallback_privacy_policy_url = fallback_privacy_policy_url

    async def record_known_touch(
        self,
        actor: ClientActor,
        *,
        raw_code: str,
        correlation_id: str | None = None,
        touched_at: datetime | None = None,
    ) -> bool:
        """Return the same non-enumerating result for invalid, unknown and inactive codes."""

        try:
            code = validate_campaign_code(raw_code)
        except CampaignValidationError:
            return False

        async with self._unit_of_work_factory() as unit_of_work:
            user = await unit_of_work.users.get_or_create_client(actor)
            if not await self._has_current_privacy(unit_of_work, user.id):
                return False
            business_client = await unit_of_work.privacy.get_client_by_user(
                user.id,
                for_update=True,
            )
            if business_client is None:
                raise RuntimeError("Business client membership is missing")
            try:
                outcome = await AcquisitionService(unit_of_work.privacy).record_touch(
                    business_client_id=business_client.id,
                    raw_code=code,
                    touched_at=touched_at,
                )
            except EntityNotFoundError:
                return False
            await unit_of_work.audit.add(
                actor_user_id=user.id,
                action="acquisition.touch_recorded",
                entity_type="client_acquisition_attribution",
                entity_id=str(outcome.attribution.id),
                changes={
                    "source_id": outcome.attribution.last_source_id,
                    "first_touch_created": outcome.first_touch_created,
                    "touch_count": outcome.attribution.touch_count,
                },
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return True

    async def _has_current_privacy(
        self,
        unit_of_work: SqlAlchemyUnitOfWork,
        user_id: int,
    ) -> bool:
        business = await unit_of_work.privacy.get_business()
        if business is None:
            return False
        url = business.privacy_policy_url or self._fallback_privacy_policy_url
        digest = business.privacy_policy_hash
        if url is None and digest is None:
            return False
        try:
            policy = PolicyDocument(
                version=business.privacy_policy_version or "published-v1",
                url=url,
                sha256=digest,
            )
        except PrivacyStateError:
            return False
        latest = await unit_of_work.privacy.latest_consent(user_id, ConsentType.PRIVACY)
        return bool(
            latest is not None
            and latest.new_value
            and policy.matches(
                version=latest.policy_version,
                url=latest.policy_url,
                sha256=latest.policy_hash,
            )
        )
