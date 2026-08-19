"""Dynamic client and admin menu visibility tests."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.enums import PortfolioDisplayMode, StaffRole
from app.keyboards.admin.features import feature_flags_keyboard
from app.keyboards.admin.main import (
    ADMIN_CLIENTS_TEXT,
    ADMIN_REVIEWS_TEXT,
    ADMIN_SERVICES_TEXT,
    ADMIN_SETTINGS_TEXT,
    ADMIN_VENDOR_SUPPORT_TEXT,
    admin_main_keyboard,
)
from app.keyboards.client.main import (
    CLIENT_MASTER_PROFILE_TEXT,
    CLIENT_MASTERS_TEXT,
    CLIENT_PAYMENTS_TEXT,
    CLIENT_PORTFOLIO_TEXT,
    CLIENT_REPEAT_TEXT,
    CLIENT_REVIEWS_TEXT,
    client_main_keyboard,
)
from app.schemas.authorization import StaffContext
from app.schemas.features import FeatureSnapshot
from app.schemas.menu import MenuCapabilities
from app.services.menu_service import MenuService


def build_uow(
    *,
    mode: PortfolioDisplayMode = PortfolioDisplayMode.INTERNAL,
    reviews: bool = True,
    master_enabled: bool = True,
    master_published: bool = True,
    feature_overrides: dict[str, bool] | None = None,
) -> MagicMock:
    unit_of_work = MagicMock()
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    unit_of_work.settings.get = AsyncMock(
        return_value=SimpleNamespace(
            portfolio_mode=mode,
            reviews_enabled=reviews,
            master_profile_enabled=master_enabled,
            waitlist_enabled=True,
        )
    )
    feature_values = {
        "online_booking": True,
        "master_selection": True,
        "waitlist": True,
        "portfolio": True,
        "reviews": True,
        "reminders": True,
        "repeat_booking": True,
        "broadcasts": False,
        "client_support": True,
    }
    feature_values.update(feature_overrides or {})
    unit_of_work.features.get = AsyncMock(return_value=SimpleNamespace(**feature_values))
    unit_of_work.master_profile.get = AsyncMock(
        return_value=SimpleNamespace(is_published=master_published)
    )
    unit_of_work.staff.has_bookable_member = AsyncMock(return_value=True)
    return unit_of_work


def texts(keyboard: object) -> set[str]:
    return {
        button.text
        for row in keyboard.keyboard  # type: ignore[attr-defined]
        for button in row
    }


def owner_context() -> StaffContext:
    return StaffContext(
        business_id=1,
        staff_member_id=1,
        user_id=1,
        telegram_id=123,
        display_name="Владелец",
        role=StaffRole.OWNER,
        is_bookable=False,
    )


def role_context(role: StaffRole) -> StaffContext:
    return owner_context().model_copy(update={"role": role})


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


@pytest.mark.asyncio
async def test_central_flags_drive_client_capabilities_and_fail_closed() -> None:
    unit_of_work = build_uow(
        feature_overrides={
            "online_booking": False,
            "master_selection": False,
            "waitlist": False,
            "portfolio": False,
            "reviews": False,
            "reminders": False,
            "repeat_booking": False,
            "client_support": False,
        }
    )
    service = MenuService(lambda: unit_of_work)  # type: ignore[arg-type]

    capabilities = await service.get_capabilities()

    assert not capabilities.online_booking_visible
    assert capabilities.masters_visible
    assert not capabilities.waitlist_visible
    assert not capabilities.portfolio_visible
    assert not capabilities.reviews_visible
    assert not capabilities.notifications_visible
    assert capabilities.payments_visible
    assert not capabilities.support_visible


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
    assert {CLIENT_PORTFOLIO_TEXT, CLIENT_REVIEWS_TEXT, CLIENT_MASTERS_TEXT} <= visible
    assert CLIENT_MASTER_PROFILE_TEXT not in visible


def test_admin_menu_hides_reviews_when_feature_is_disabled() -> None:
    keyboard = admin_main_keyboard(
        MenuCapabilities(
            portfolio_visible=False,
            reviews_visible=False,
            master_profile_visible=False,
        ),
        owner_context(),
    )

    assert ADMIN_REVIEWS_TEXT not in texts(keyboard)


def test_default_keyboards_remain_backward_compatible() -> None:
    assert CLIENT_REVIEWS_TEXT in texts(client_main_keyboard())
    assert CLIENT_PAYMENTS_TEXT in texts(client_main_keyboard())
    assert CLIENT_REPEAT_TEXT not in texts(client_main_keyboard())


def test_removed_repeat_booking_is_hidden_from_admin_features() -> None:
    snapshot = FeatureSnapshot.model_validate(
        {field: True for field in FeatureSnapshot.model_fields}
    )

    keyboard = feature_flags_keyboard(snapshot, can_manage=True)
    labels = {button.text for row in keyboard.inline_keyboard for button in row}

    assert all("Повторная запись" not in label for label in labels)


def test_admin_keyboard_fails_closed_without_verified_staff_context() -> None:
    assert not texts(admin_main_keyboard())


def test_admin_keyboard_uses_role_permissions_not_telegram_id() -> None:
    capabilities = MenuCapabilities(broadcasts_visible=True)

    owner_labels = texts(admin_main_keyboard(capabilities, role_context(StaffRole.OWNER)))
    receptionist_labels = texts(
        admin_main_keyboard(capabilities, role_context(StaffRole.RECEPTIONIST))
    )
    master_labels = texts(admin_main_keyboard(capabilities, role_context(StaffRole.MASTER)))

    assert {ADMIN_SERVICES_TEXT, ADMIN_SETTINGS_TEXT, ADMIN_CLIENTS_TEXT} <= owner_labels
    assert ADMIN_CLIENTS_TEXT in receptionist_labels
    assert ADMIN_SERVICES_TEXT not in receptionist_labels
    assert ADMIN_SETTINGS_TEXT not in receptionist_labels
    assert ADMIN_VENDOR_SUPPORT_TEXT in owner_labels
    assert ADMIN_VENDOR_SUPPORT_TEXT not in receptionist_labels
    assert not master_labels
