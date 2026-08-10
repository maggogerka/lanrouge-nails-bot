"""Task-local, server-derived staff authorization context."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from app.domain.enums import StaffRole
from app.schemas.authorization import StaffContext

LEGACY_ADMIN_ROLES = frozenset(
    {
        StaffRole.OWNER,
        StaffRole.MANAGER,
        StaffRole.RECEPTIONIST,
    }
)

_staff_context: ContextVar[StaffContext | None] = ContextVar(
    "staff_context",
    default=None,
)
_db_staff_authorization_required: ContextVar[bool] = ContextVar(
    "db_staff_authorization_required",
    default=False,
)


def get_staff_context() -> StaffContext | None:
    """Return the fresh DB context bound to the current Telegram update."""

    return _staff_context.get()


def is_db_staff_authorization_required() -> bool:
    """Tell legacy services whether the current call came from protected runtime."""

    return _db_staff_authorization_required.get()


@contextmanager
def db_staff_authorization_required_scope() -> Iterator[None]:
    """Mark one production update as DB-only, including unprotected routers."""

    token = _db_staff_authorization_required.set(True)
    try:
        yield
    finally:
        _db_staff_authorization_required.reset(token)


@contextmanager
def staff_authorization_scope(context: StaffContext) -> Iterator[None]:
    """Bind one verified context and reset it even if the handler fails."""

    context_token = _staff_context.set(context)
    required_token = _db_staff_authorization_required.set(True)
    try:
        yield
    finally:
        _db_staff_authorization_required.reset(required_token)
        _staff_context.reset(context_token)
