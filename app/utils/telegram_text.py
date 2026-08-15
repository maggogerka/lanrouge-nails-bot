"""Pure Telegram text-length rules shared by schemas and transport code."""

from __future__ import annotations

from dataclasses import dataclass
from html import unescape
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


def _take_utf16_prefix(value: str, limit: int) -> tuple[str, str]:
    used = 0
    for index, character in enumerate(value):
        size = len(character.encode("utf-16-le")) // 2
        if used + size > limit:
            return value[:index], value[index:]
        used += size
    return value, ""


@dataclass(slots=True)
class _OpenTag:
    name: str
    source: str


class _TelegramHtmlSplitter(HTMLParser):
    """Split valid HTML without cutting entities or leaving tags unbalanced."""

    def __init__(self, limit: int) -> None:
        super().__init__(convert_charrefs=False)
        self.limit = limit
        self.visible_length = 0
        self.parts: list[str] = []
        self.chunks: list[str] = []
        self.open_tags: list[_OpenTag] = []

    def _flush(self) -> None:
        if self.visible_length == 0:
            return
        self.parts.extend(f"</{tag.name}>" for tag in reversed(self.open_tags))
        self.chunks.append("".join(self.parts))
        self.parts = [tag.source for tag in self.open_tags]
        self.visible_length = 0

    def _append_visible(self, source: str, visible: str) -> None:
        size = telegram_text_length(visible)
        if size <= self.limit - self.visible_length:
            self.parts.append(source)
            self.visible_length += size
            return
        if source != visible:
            self._flush()
            self.parts.append(source)
            self.visible_length = size
            return
        remainder = visible
        while remainder:
            available = self.limit - self.visible_length
            prefix, remainder = _take_utf16_prefix(remainder, available)
            if prefix:
                self.parts.append(prefix)
                self.visible_length += telegram_text_length(prefix)
            if remainder:
                self._flush()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        source = self.get_starttag_text() or f"<{tag}>"
        self.parts.append(source)
        self.open_tags.append(_OpenTag(tag, source))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        self.parts.append(self.get_starttag_text() or f"<{tag}/>")

    def handle_endtag(self, tag: str) -> None:
        self.parts.append(f"</{tag}>")
        if self.open_tags and self.open_tags[-1].name == tag:
            self.open_tags.pop()

    def handle_data(self, data: str) -> None:
        self._append_visible(data, data)

    def handle_entityref(self, name: str) -> None:
        source = f"&{name};"
        self._append_visible(source, unescape(source))

    def handle_charref(self, name: str) -> None:
        source = f"&#{name};"
        self._append_visible(source, unescape(source))


def split_telegram_html(value: str, *, limit: int = TELEGRAM_MESSAGE_LIMIT) -> list[str]:
    """Return non-empty, independently valid HTML messages within Telegram's limit."""

    if limit <= 0:
        raise ValueError("Telegram text limit must be positive")
    if telegram_text_length(value, html=True) <= limit:
        return [value]
    parser = _TelegramHtmlSplitter(limit)
    parser.feed(value)
    parser.close()
    parser._flush()
    return parser.chunks
