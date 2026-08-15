"""Safe Telegram HTML rules for editable business welcome messages."""

from __future__ import annotations

from html import escape
from html.parser import HTMLParser
from urllib.parse import urlsplit

from app.domain.errors import DomainError

WELCOME_TEXT_MAX_LENGTH = 3500
_SIMPLE_TAGS = frozenset(
    {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre"}
)


class WelcomeContentError(DomainError):
    """Welcome content contains unsafe or unsupported Telegram HTML."""


class _TelegramHtmlSanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.output: list[str] = []
        self.stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        normalized = tag.casefold()
        if normalized in _SIMPLE_TAGS and not attrs:
            self.output.append(f"<{normalized}>")
        elif normalized == "a" and len(attrs) == 1 and attrs[0][0].casefold() == "href":
            href = attrs[0][1] or ""
            parsed = urlsplit(href)
            if (
                parsed.scheme != "https"
                or parsed.hostname is None
                or parsed.username is not None
                or parsed.password is not None
            ):
                raise WelcomeContentError("welcome links must use safe HTTPS URLs")
            self.output.append(f'<a href="{escape(href, quote=True)}">')
        else:
            raise WelcomeContentError(f"unsupported welcome formatting tag: {normalized}")
        self.stack.append(normalized)

    def handle_endtag(self, tag: str) -> None:
        normalized = tag.casefold()
        if not self.stack or self.stack[-1] != normalized:
            raise WelcomeContentError("welcome formatting tags must be properly nested")
        self.stack.pop()
        self.output.append(f"</{normalized}>")

    def handle_data(self, data: str) -> None:
        self.output.append(escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        self.output.append(escape(f"&{name};", quote=False))

    def handle_charref(self, name: str) -> None:
        self.output.append(escape(f"&#{name};", quote=False))

    def close(self) -> None:
        super().close()
        if self.stack:
            raise WelcomeContentError("welcome formatting tag is not closed")


def sanitize_welcome_html(value: str) -> str:
    """Normalize, validate and escape a bounded Telegram HTML message."""

    normalized = value.strip()
    if not normalized:
        raise WelcomeContentError("welcome text must not be empty")
    if len(normalized) > WELCOME_TEXT_MAX_LENGTH:
        raise WelcomeContentError(
            f"welcome text must not exceed {WELCOME_TEXT_MAX_LENGTH} characters"
        )
    parser = _TelegramHtmlSanitizer()
    try:
        parser.feed(normalized)
        parser.close()
    except (ValueError, AssertionError) as exc:
        raise WelcomeContentError("welcome formatting is invalid") from exc
    return "".join(parser.output)


def default_welcome_html(business_name: str) -> str:
    """Build the universal public fallback without vertical-specific wording."""

    return (
        f"Добро пожаловать в <b>{escape(business_name)}</b>!\n\n"
        "Здесь можно выбрать услугу и записаться на удобное время."
    )
