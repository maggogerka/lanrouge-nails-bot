"""Persistent audit metadata must redact sensitive values."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.repositories.audit_repository import AuditRepository


@pytest.mark.asyncio
async def test_audit_repository_redacts_nested_sensitive_values() -> None:
    session = MagicMock()
    session.flush = AsyncMock()
    repository = AuditRepository(session)

    await repository.add(
        actor_user_id=None,
        action="client_note.created",
        entity_type="client_note",
        entity_id="7",
        changes={
            "client_id": 3,
            "note_text": "private",
            "nested": {"phone": "+79991234567"},
        },
        correlation_id="request-7",
    )

    entry = session.add.call_args.args[0]
    assert entry.actor_user_id is None
    assert entry.changes == {
        "client_id": 3,
        "note_text": "[redacted]",
        "nested": {"phone": "[redacted]"},
    }
