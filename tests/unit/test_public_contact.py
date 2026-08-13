"""Selected-master and business links remain safe and correctly scoped."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.database.models.business import StaffMember
from app.domain.enums import StaffRole
from app.schemas.public_links import MAX_PUBLIC_LINKS, PublicLink, normalize_public_link_mapping
from app.services.public_contact import resolve_staff_contact_url


def test_public_links_require_https_and_are_bounded() -> None:
    assert PublicLink(label=" Telegram ", url=" https://t.me/example ").model_dump() == {
        "label": "Telegram",
        "url": "https://t.me/example",
    }
    with pytest.raises(ValidationError):
        PublicLink(label="Telegram", url="http://t.me/example")
    with pytest.raises(ValueError, match="не более"):
        normalize_public_link_mapping(
            {
                f"Link {index}": f"https://example.test/{index}"
                for index in range(MAX_PUBLIC_LINKS + 1)
            }
        )


@pytest.mark.asyncio
async def test_master_configured_link_precedes_telegram_identity() -> None:
    session = MagicMock()
    session.get = AsyncMock()
    member = StaffMember(
        business_id=1,
        display_name="Master",
        role=StaffRole.MASTER,
        settings={"social_links": {"WhatsApp": "https://wa.me/79990000000"}},
        user_id=7,
    )

    assert await resolve_staff_contact_url(session, member) == "https://wa.me/79990000000"
    session.get.assert_not_awaited()


@pytest.mark.asyncio
async def test_master_bound_telegram_is_safe_fallback() -> None:
    session = MagicMock()
    session.get = AsyncMock(return_value=SimpleNamespace(username="master_name", telegram_id=42))
    member = StaffMember(
        business_id=1,
        display_name="Master",
        role=StaffRole.MASTER,
        settings={},
        user_id=7,
    )

    assert await resolve_staff_contact_url(session, member) == "https://t.me/master_name"
