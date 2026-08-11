"""Read-only white-label projections independent of legacy singleton branding."""

from __future__ import annotations

from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models.business import Business, StaffMember
from app.database.models.settings import BusinessSettings
from app.domain.errors import EntityNotFoundError
from app.domain.tenancy import DEFAULT_BUSINESS_ID
from app.domain.welcome import default_welcome_html
from app.schemas.presentation import BusinessPresentation, PublicMasterPresentation

SessionFactory = async_sessionmaker[AsyncSession]


class PresentationService:
    """Resolve public business identity and master cards from persisted data."""

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        business_id: int = DEFAULT_BUSINESS_ID,
        fallback_privacy_policy_url: str | None = None,
    ) -> None:
        if business_id <= 0:
            raise ValueError("business_id must be positive")
        self._session_factory = session_factory
        self._business_id = business_id
        self._fallback_privacy_policy_url = self._safe_url(fallback_privacy_policy_url)

    async def get_business(self) -> BusinessPresentation:
        async with self._session_factory() as session:
            business = await session.get(Business, self._business_id)
            if business is None:
                raise EntityNotFoundError("Business presentation is not configured")
            settings = (
                await session.scalars(
                    select(BusinessSettings).where(
                        BusinessSettings.business_id == self._business_id
                    )
                )
            ).one_or_none()
            return self._business_projection(business, settings)

    async def list_bookable_masters(self) -> tuple[PublicMasterPresentation, ...]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(StaffMember)
                .where(
                    StaffMember.business_id == self._business_id,
                    StaffMember.is_active.is_(True),
                    StaffMember.is_bookable.is_(True),
                    StaffMember.archived_at.is_(None),
                )
                .order_by(StaffMember.sort_order, StaffMember.display_name, StaffMember.id)
            )
            return tuple(
                PublicMasterPresentation(
                    staff_member_id=member.id,
                    display_name=member.display_name,
                    bio=member.bio,
                    specialization=member.specialization,
                    telegram_photo_file_id=member.telegram_photo_file_id,
                )
                for member in rows.all()
            )

    def _business_projection(
        self,
        business: Business,
        settings: BusinessSettings | None,
    ) -> BusinessPresentation:
        return BusinessPresentation(
            business_id=business.id,
            display_name=business.display_name or self._settings_value(settings, "business_name"),
            business_type=business.business_type,
            timezone=business.timezone,
            currency=business.currency,
            address=business.address or self._settings_value(settings, "address"),
            map_url=self._safe_url(business.map_url or self._settings_value(settings, "map_url")),
            contact_phone=business.contact_phone,
            contact_email=business.contact_email,
            logo_telegram_file_id=business.logo_telegram_file_id,
            support_name=business.client_support_name,
            support_url=self._safe_url(
                business.client_support_url or self._settings_value(settings, "master_telegram_url")
            ),
            support_hours=business.client_support_hours,
            support_instructions=business.client_support_instructions,
            privacy_policy_url=self._safe_url(business.privacy_policy_url)
            or self._fallback_privacy_policy_url,
            terms_url=self._safe_url(business.terms_url),
            welcome_text=(
                business.welcome_published_text or default_welcome_html(business.display_name)
            ),
            welcome_photo_file_id=business.welcome_published_photo_file_id,
        )

    @staticmethod
    def _settings_value(settings: BusinessSettings | None, field: str) -> str | None:
        if settings is None:
            return None
        value = getattr(settings, field, None)
        return value if isinstance(value, str) and value else None

    @staticmethod
    def _safe_url(value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        parsed = urlparse(normalized)
        if parsed.scheme != "https" or not parsed.hostname:
            return None
        return normalized
