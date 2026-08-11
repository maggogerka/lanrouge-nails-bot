"""Stable text contracts for bootstrap Telegram commands."""

from app.handlers.client.onboarding import privacy_text
from app.handlers.common.commands import start_text, whoami_text


def test_start_text_has_brand_and_no_dead_menu() -> None:
    text = start_text("Новая студия")

    assert "Новая студия" in text
    assert "lanrouge" not in text.lower()
    assert "/whoami" in text
    assert "Записаться" not in text


def test_whoami_text_contains_only_supplied_numeric_id() -> None:
    text = whoami_text(123456789)

    assert text == "Ваш Telegram ID: <code>123456789</code>"


def test_privacy_copy_is_universal_because_welcome_is_rendered_separately() -> None:
    text = privacy_text("Новая <студия>")

    assert "обработку данных" in text
    assert "Новая" not in text
    assert "lanrouge" not in text.lower()
