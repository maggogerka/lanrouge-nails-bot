"""Append-only administrative audit persistence."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import AuditLog


class AuditRepository:
    """Append safe changes to the audit log."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(
        self,
        *,
        actor_user_id: int,
        action: str,
        entity_type: str,
        entity_id: str,
        changes: dict[str, object],
        correlation_id: str | None,
    ) -> AuditLog:
        entry = AuditLog(
            actor_user_id=actor_user_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            changes=changes,
            correlation_id=correlation_id,
        )
        self._session.add(entry)
        await self._session.flush()
        return entry
