"""Shared PostgreSQL-aware SQLAlchemy type factories."""

from __future__ import annotations

from enum import Enum as PythonEnum

from sqlalchemy import Enum


def database_enum[EnumT: PythonEnum](enum_class: type[EnumT], *, name: str) -> Enum:
    """Persist enum values (lowercase API strings), not Python member names."""

    return Enum(
        enum_class,
        name=name,
        native_enum=True,
        validate_strings=True,
        values_callable=lambda members: [str(member.value) for member in members],
    )
