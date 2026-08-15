"""Small deterministic pagination helpers for bounded Telegram screens."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SequencePage[T]:
    items: tuple[T, ...]
    page: int
    pages: int
    total: int


def paginate_sequence[T](
    items: Sequence[T],
    *,
    page: int,
    page_size: int,
) -> SequencePage[T]:
    """Clamp stale callback pages and return no more than ``page_size`` items."""

    if page_size <= 0:
        raise ValueError("page_size must be positive")
    total = len(items)
    pages = max(1, (total + page_size - 1) // page_size)
    current = min(max(page, 1), pages)
    start = (current - 1) * page_size
    return SequencePage(tuple(items[start : start + page_size]), current, pages, total)
