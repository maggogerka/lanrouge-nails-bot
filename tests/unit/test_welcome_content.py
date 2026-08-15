"""Safe, versioned welcome draft contracts."""

import pytest

from app.domain.welcome import (
    WelcomeContentError,
    default_welcome_html,
    sanitize_welcome_html,
)


def test_safe_telegram_html_is_preserved_and_plain_text_is_escaped() -> None:
    assert sanitize_welcome_html("<b>Привет</b> & запись") == ("<b>Привет</b> &amp; запись")
    assert sanitize_welcome_html('<a href="https://example.test/path?a=1&b=2">Сайт</a>') == (
        '<a href="https://example.test/path?a=1&amp;b=2">Сайт</a>'
    )


@pytest.mark.parametrize(
    "value",
    (
        '<script>alert("x")</script>',
        '<a href="javascript:alert(1)">link</a>',
        "<b>not closed",
        "<b><i>wrong</b></i>",
        "",
    ),
)
def test_unsafe_or_invalid_welcome_html_is_rejected(value: str) -> None:
    with pytest.raises(WelcomeContentError):
        sanitize_welcome_html(value)


def test_default_welcome_escapes_business_name_and_is_vertical_neutral() -> None:
    result = default_welcome_html("Салон <Example>")
    assert "&lt;Example&gt;" in result
    assert "записаться" in result.casefold()
