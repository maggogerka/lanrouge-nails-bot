"""Validated bounded pagination shared by growing v0.2.0 lists."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field


class PageRequest(BaseModel):
    """One-based page input with a hard upper bound for repository queries."""

    page: Annotated[int, Field(ge=1)] = 1
    page_size: Annotated[int, Field(ge=1, le=50)] = 10

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class Page[ItemT](BaseModel):
    """A stable page plus enough metadata to validate navigation callbacks."""

    items: list[ItemT]
    total: Annotated[int, Field(ge=0)]
    page: Annotated[int, Field(ge=1)]
    page_size: Annotated[int, Field(ge=1, le=50)]

    @property
    def pages(self) -> int:
        if self.total == 0:
            return 1
        return (self.total + self.page_size - 1) // self.page_size

    @property
    def has_previous(self) -> bool:
        return self.page > 1

    @property
    def has_next(self) -> bool:
        return self.page < self.pages
