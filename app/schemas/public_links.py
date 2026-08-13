"""Validated public links used by business and staff profiles."""

from __future__ import annotations

from typing import Annotated
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_PUBLIC_LINKS = 5


def validate_https_url(value: str) -> str:
    """Return a trimmed, public HTTPS URL without embedded credentials."""

    normalized = value.strip()
    parsed = urlsplit(normalized)
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("ссылка должна начинаться с https:// и не содержать логин или пароль")
    return normalized


class PublicLink(BaseModel):
    """One owner-configured link safe to place in a Telegram URL button."""

    model_config = ConfigDict(frozen=True)

    label: Annotated[str, Field(min_length=1, max_length=64)]
    url: Annotated[str, Field(max_length=2048)]

    @field_validator("label", mode="before")
    @classmethod
    def normalize_label(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("url")
    @classmethod
    def url_is_public_https(cls, value: str) -> str:
        return validate_https_url(value)


def normalize_public_link_mapping(value: object) -> dict[str, str]:
    """Validate a JSON-compatible label-to-URL mapping and cap its size."""

    if not isinstance(value, dict):
        raise ValueError("источники должны быть списком названий и ссылок")
    if len(value) > MAX_PUBLIC_LINKS:
        raise ValueError(f"можно добавить не более {MAX_PUBLIC_LINKS} ссылок")
    normalized: dict[str, str] = {}
    for raw_label, raw_url in value.items():
        link = PublicLink(label=raw_label, url=raw_url)
        folded = link.label.casefold()
        if any(existing.casefold() == folded for existing in normalized):
            raise ValueError("названия ссылок не должны повторяться")
        normalized[link.label] = link.url
    return normalized


def public_links_from_mapping(value: object) -> tuple[PublicLink, ...]:
    """Project possibly old JSON data fail-closed, omitting malformed entries."""

    if not isinstance(value, dict):
        return ()
    links: list[PublicLink] = []
    for label, url in value.items():
        if len(links) >= MAX_PUBLIC_LINKS:
            break
        try:
            links.append(PublicLink(label=label, url=url))
        except (TypeError, ValueError):
            continue
    return tuple(links)
