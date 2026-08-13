"""White-label presentation remains database-driven and URL-safe."""

from unittest.mock import MagicMock

from app.database.models.business import Business
from app.database.models.settings import BusinessSettings
from app.domain.enums import BusinessStatus, BusinessType
from app.services.presentation_service import PresentationService


def test_business_projection_prefers_tenant_branding_and_sanitizes_links() -> None:
    business = Business(
        id=1,
        slug="new-studio",
        display_name="Новая студия",
        business_type=BusinessType.SALON,
        status=BusinessStatus.ACTIVE,
        timezone="Europe/Moscow",
        currency="RUB",
        address=None,
        map_url="javascript:alert(1)",
        contact_phone="+79990000000",
        contact_email="hello@example.test",
        client_support_url="https://support.example.test/chat",
        privacy_policy_url=None,
        terms_url="http://insecure.example.test/terms",
        instance_id="instance-1",
        welcome_published_text="<b>Публичное приветствие</b>",
        welcome_published_photo_file_id="welcome-photo",
    )
    settings = BusinessSettings(
        id=1,
        business_id=1,
        business_name="Старое имя",
        address="Адрес из настроек",
        map_url="https://maps.example.test/place",
        master_telegram_url="https://t.me/configured_support",
    )
    service = PresentationService(
        MagicMock(),  # type: ignore[arg-type]
        fallback_privacy_policy_url="https://legal.example.test/privacy",
    )

    projection = service._business_projection(business, settings)

    assert projection.display_name == "Новая студия"
    assert projection.address is None
    assert projection.map_url is None
    assert projection.support_url == "https://support.example.test/chat"
    assert projection.privacy_policy_url == "https://legal.example.test/privacy"
    assert projection.terms_url is None
    assert projection.welcome_text == "<b>Публичное приветствие</b>"
    assert projection.welcome_photo_file_id == "welcome-photo"
    assert "lanrouge" not in projection.model_dump_json().lower()


def test_only_https_links_are_publicly_rendered() -> None:
    assert PresentationService._safe_url("https://example.test/path")
    assert PresentationService._safe_url("http://example.test/path") is None
    assert PresentationService._safe_url("tg://resolve?domain=owner") is None
    assert PresentationService._safe_url("javascript:alert(1)") is None
