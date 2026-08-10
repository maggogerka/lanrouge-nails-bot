"""Explicit tenant scope for the isolated v0.4 deployment."""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_BUSINESS_ID = 1
DEFAULT_STAFF_MEMBER_ID = 1


@dataclass(frozen=True, slots=True)
class BusinessContext:
    """Server-resolved tenant identity passed into application operations."""

    business_id: int

    def __post_init__(self) -> None:
        if self.business_id <= 0:
            raise ValueError("business_id must be positive")


DEFAULT_BUSINESS_CONTEXT = BusinessContext(DEFAULT_BUSINESS_ID)
