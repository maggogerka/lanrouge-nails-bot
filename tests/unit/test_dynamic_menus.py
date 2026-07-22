"""Dynamic client and admin menu visibility tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.enums import PortfolioDisplayMode
from app.keyboards.admin.main import ADMIN_REVIEWS_TEXT, admin_main_keyboard
from app.keyboards.client.main import (
    CLIENT_MASTER_PROFILE_TEXT,
    CLIENT_PORTFOLIO_TEXT,
    CLIENT_REVIEWS_TEXT,
    client_main_keyboard,
)
from app.schemas.menu import MenuCapabilities
from app.services.menu_service import MenuService


def build_uow(
    *,
    mode: PortfolioDisplayMode = PortfolioDisplayMode.INTERNAL,
    reviews: bool = True,
    master_enabled: bool = True,
    master_published: bool = True,
) -> MagicMock:
    unit_of_work = MagicMock()
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    unit_of_work.settings.get = AsyncMock(
        return_value=SimpleNamespace(
            portfolio_mode=mode,
            reviews_enabled=reviews,
            master_profile_enabled=master_enabled,
        )
    )
    unit_of_work.master_profile.get = AsyncMock(
        return_value=SimpleNamespace(is_published=master_published)
    )
    return unit_of_work


def texts(keyboard: object) -> set[str]:
    return {
        button.text
        for row in keyboard.keyboard  # type: ignore[attr-defined]
        for button in row
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (PortfolioDisplayMode.INTERNAL, True),
        (PortfolioDisplayMode.EXTERNAL_LINK, True),
        (PortfolioDisplayMode.DISABLED, False),
    ],
)
async def test_portfolio_visibility_follows_display_mode(
    mode: PortfolioDisplayMode, expected: bool
) -> None:
    unit_of_work = build_uow(mode=mode)
    service = MenuService(lambda: unit_of_work)  # type: ignore[arg-type]

    capabilities = await service.get_capabilities()

    assert capabilities.portfolio_visible is expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("enabled", "published", "expected"),
    [(True, True, True), (True, False, False), (False, True, False), (False, False, False)],
)
async def test_master_profile_requires_both_switch_and_publication(
    enabled: bool, published: bool, expected: bool
) -> None:
    unit_of_work = build_uow(master_enabled=enabled, master_published=published)
    service = MenuService(lambda: unit_of_work)  # type: ignore[arg-type]

    capabilities = await service.get_capabilities()

    assert capabilities.master_profile_visible is expected


@pytest.mark.asyncio
async def test_missing_profile_hides_master_button() -> None:
    unit_of_work = build_uow()
    unit_of_work.master_profile.get = AsyncMock(return_value=None)
    service = MenuService(lambda: unit_of_work)  # type: ignore[arg-type]

    assert not (await service.get_capabilities()).master_profile_visible


@pytest.mark.asyncio
async def test_missing_settings_fails_closed() -> None:
    unit_of_work = build_uow()
    unit_of_work.settings.get = AsyncMock(return_value=None)
    service = MenuService(lambda: unit_of_work)  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="settings row is missing"):
        await service.get_capabilities()


def test_client_menu_hides_all_disabled_optional_sections() -> None:
    keyboard = client_main_keyboard(
        MenuCapabilities(
            portfolio_visible=False,
            reviews_visible=False,
            master_profile_visible=False,
        )
    )

    visible = texts(keyboard)
    assert CLIENT_PORTFOLIO_TEXT not in visible
    assert CLIENT_REVIEWS_TEXT not in visible
    assert CLIENT_MASTER_PROFILE_TEXT not in visible


def test_client_menu_shows_all_enabled_optional_sections() -> None:
    keyboard = client_main_keyboard(
        MenuCapabilities(
            portfolio_visible=True,
            reviews_visible=True,
            master_profile_visible=True,
        )
    )

    visible = texts(keyboard)
    assert {CLIENT_PORTFOLIO_TEXT, CLIENT_REVIEWS_TEXT, CLIENT_MASTER_PROFILE_TEXT} <= visible


def test_admin_menu_hides_reviews_when_feature_is_disabled() -> None:
    keyboard = admin_main_keyboard(
        MenuCapabilities(
            portfolio_visible=False,
            reviews_visible=False,
            master_profile_visible=False,
        )
    )

    assert ADMIN_REVIEWS_TEXT not in texts(keyboard)


def test_default_keyboards_remain_backward_compatible() -> None:
    assert CLIENT_REVIEWS_TEXT in texts(client_main_keyboard())
    assert ADMIN_REVIEWS_TEXT in texts(admin_main_keyboard())
