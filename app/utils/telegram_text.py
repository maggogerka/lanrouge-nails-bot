"""Pure Telegram text-length rules shared by schemas and transport code."""

from __future__ import annotations

from html.parser import HTMLParser

TELEGRAM_MESSAGE_LIMIT = 4096
TELEGRAM_CAPTION_LIMIT = 1024


class _VisibleHtmlText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _visible_html_text(value: str) -> str:
    parser = _VisibleHtmlText()
    parser.feed(value)
    parser.close()
    return "".join(parser.parts)


def telegram_text_length(value: str, *, html: bool = False) -> int:
    """Count UTF-16 code units, matching Telegram entity offset semantics."""

    visible = _visible_html_text(value) if html else value
    return len(visible.encode("utf-16-le")) // 2


def fits_telegram_caption(value: str, *, html: bool = False) -> bool:
    return telegram_text_length(value, html=html) <= TELEGRAM_CAPTION_LIMIT


def require_telegram_message(value: str, *, html: bool = False) -> str:
    if telegram_text_length(value, html=html) > TELEGRAM_MESSAGE_LIMIT:
        raise ValueError(f"Telegram message exceeds {TELEGRAM_MESSAGE_LIMIT} UTF-16 units")
    return value
