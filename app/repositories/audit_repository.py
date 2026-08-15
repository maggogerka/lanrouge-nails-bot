"""Append-only administrative audit persistence."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AuditLog
from app.domain.tenancy import DEFAULT_BUSINESS_ID
from app.repositories.scoped import TenantScopedRepository

_SENSITIVE_KEY_PARTS = (
    "database_url",
    "dsn",
    "password",
    "phone",
    "token",
    "note_text",
    "review_text",
    "broadcast_text",
)


def _safe_changes(value: object, *, key: str = "") -> object:
    """Recursively redact fields that must never enter persistent audit metadata."""

    normalized_key = key.casefold()
    if any(part in normalized_key for part in _SENSITIVE_KEY_PARTS):
        return "[redacted]"
    if isinstance(value, dict):
        return {
            str(item_key): _safe_changes(item, key=str(item_key))
            for item_key, item in value.items()
        }
    if isinstance(value, list):
        return [_safe_changes(item, key=key) for item in value]
    if isinstance(value, tuple):
        return [_safe_changes(item, key=key) for item in value]
    return value


class AuditRepository(TenantScopedRepository):
    """Append safe changes to the audit log."""

    def __init__(self, session: AsyncSession, business_id: int = DEFAULT_BUSINESS_ID) -> None:
        super().__init__(session, business_id)

    async def add(
        self,
        *,
        actor_user_id: int | None,
        action: str,
        entity_type: str,
        entity_id: str,
        changes: dict[str, object],
        correlation_id: str | None,
        business_id: int | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            business_id=business_id or self.business_id,
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            changes=_safe_changes(changes),
            correlation_id=correlation_id,
        )
        self._session.add(entry)
        await self._session.flush()
        return entry
