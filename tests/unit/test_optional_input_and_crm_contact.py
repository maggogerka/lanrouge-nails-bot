"""One-tap optional input and safe CRM contact presentation."""

from app.handlers.admin.crm import _render_card
from app.keyboards.admin.crm import client_card_keyboard
from app.keyboards.common.optional_input import (
    NO_COMMENT_TEXT,
    OPTIONAL_SKIP_TEXT,
    is_optional_skip,
    optional_input_keyboard,
)
from app.schemas.crm import ClientCardView, safe_telegram_profile_url


def card(*, username: str | None = "valid_user") -> ClientCardView:
    return ClientCardView(
        id=10,
        telegram_id=123456789,
        display_name="Клиент",
        username=username,
        telegram_profile_url=safe_telegram_profile_url(username),
        masked_phone="***3210",
        phone="+79991233210",
        marketing_subscribed=False,
        is_blocked=False,
        is_self_booking_blocked=False,
        completed_visits=0,
        cancellations=0,
        no_shows=0,
        appointments_total=0,
        appointments=[],
        tags=[],
        notes=[],
    )


def test_skip_button_and_legacy_dash_have_identical_semantics() -> None:
    assert is_optional_skip("-")
    assert is_optional_skip(OPTIONAL_SKIP_TEXT)
    assert is_optional_skip(NO_COMMENT_TEXT)
    assert not is_optional_skip("нужный текст")
    keyboard = optional_input_keyboard(no_comment=True)
    assert keyboard.keyboard[0][0].text == NO_COMMENT_TEXT


def test_crm_card_has_numeric_id_safe_profile_link_and_permissioned_phone() -> None:
    value = card()
    hidden = _render_card(value, show_phone=False)
    visible = _render_card(value, show_phone=True)

    assert "123456789" in hidden
    assert "@valid_user" in hidden
    assert "https://t.me/valid_user" in hidden
    assert "+79991233210" not in hidden
    assert "+79991233210" in visible
    assert client_card_keyboard(value).inline_keyboard[0][0].url == ("https://t.me/valid_user")


def test_missing_or_unsafe_username_has_clear_marker_and_no_url_button() -> None:
    missing = card(username=None)
    unsafe = card(username="bad/name")

    assert "Username: не указан" in _render_card(missing)
    assert safe_telegram_profile_url("bad/name") is None
    assert all(row[0].url is None for row in client_card_keyboard(unsafe).inline_keyboard)
