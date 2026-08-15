"""Broadcast delivery leases, live consent checks and sent idempotency."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.models import Broadcast, BroadcastRecipient, User
from app.domain.enums import (
    BroadcastAudienceType,
    BroadcastButtonType,
    BroadcastRecipientStatus,
    BroadcastStatus,
)
from app.services.broadcast_delivery_service import BroadcastDeliveryService

NOW = datetime(2026, 7, 22, 9, tzinfo=UTC)


def campaign() -> Broadcast:
    return Broadcast(
        id=31,
        title="Campaign",
        text="Safe plain text",
        status=BroadcastStatus.SENDING,
        audience_type=BroadcastAudienceType.ALL_SUBSCRIBED,
        audience_parameters={},
        button_type=BroadcastButtonType.NONE,
        created_by=9,
        scheduled_at=NOW,
    )


def recipient(status: BroadcastRecipientStatus = BroadcastRecipientStatus.PROCESSING):
    return BroadcastRecipient(
        id=41,
        broadcast_id=31,
        user_id=5,
        status=status,
        attempts=1,
        scheduled_at=NOW,
        available_at=NOW,
        locked_at=NOW,
        locked_by="worker",
    )


def build_uow(*, marketing: bool = True) -> tuple[MagicMock, BroadcastRecipient, User]:
    unit_of_work = MagicMock()
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    target_recipient = recipient()
    user = User(
        id=5,
        telegram_id=101,
        marketing_consent_at=NOW if marketing else None,
        marketing_unsubscribed_at=None if marketing else NOW,
        is_blocked=False,
    )
    unit_of_work.broadcasts.get_recipient = AsyncMock(return_value=target_recipient)
    unit_of_work.broadcasts.get = AsyncMock(return_value=campaign())
    unit_of_work.broadcasts.list_media = AsyncMock(return_value=[])
    unit_of_work.broadcasts.status_counts = AsyncMock(
        return_value={BroadcastRecipientStatus.PROCESSING: 1}
    )
    unit_of_work.users.get_by_id = AsyncMock(return_value=user)
    unit_of_work.users.mark_blocked = AsyncMock()
    unit_of_work.features.get = AsyncMock(return_value=SimpleNamespace(broadcasts=True))
    unit_of_work.session.flush = AsyncMock()
    unit_of_work.commit = AsyncMock()
    return unit_of_work, target_recipient, user


@pytest.mark.asyncio
async def test_unsubscribed_snapshot_member_is_skipped_before_io() -> None:
    unit_of_work, target, _ = build_uow(marketing=False)
    service = BroadcastDeliveryService(
        lambda: unit_of_work,
        lease_seconds=120,
        max_attempts=5,  # type: ignore[arg-type]
    )

    assert await service.prepare_delivery(41, "worker") is None
    assert target.status is BroadcastRecipientStatus.UNSUBSCRIBED


@pytest.mark.asyncio
async def test_sent_recipient_is_never_prepared_or_marked_twice() -> None:
    unit_of_work, target, _ = build_uow()
    service = BroadcastDeliveryService(
        lambda: unit_of_work,
        lease_seconds=120,
        max_attempts=5,  # type: ignore[arg-type]
    )

    delivery = await service.prepare_delivery(41, "worker")
    assert delivery is not None
    unit_of_work.broadcasts.status_counts.return_value = {BroadcastRecipientStatus.SENT: 1}
    assert await service.mark_sent(41, "worker", telegram_message_id=777)
    assert target.status is BroadcastRecipientStatus.SENT

    assert await service.prepare_delivery(41, "worker") is None
    assert not await service.mark_sent(41, "worker", telegram_message_id=778)
    assert target.telegram_message_id == 777


@pytest.mark.asyncio
async def test_forbidden_marks_user_and_snapshot_recipient_blocked() -> None:
    unit_of_work, target, user = build_uow()
    service = BroadcastDeliveryService(
        lambda: unit_of_work,
        lease_seconds=120,
        max_attempts=5,  # type: ignore[arg-type]
    )

    assert await service.mark_blocked(41, "worker")
    assert target.status is BroadcastRecipientStatus.BLOCKED
    unit_of_work.users.mark_blocked.assert_awaited_once_with(user)


@pytest.mark.asyncio
async def test_disabled_broadcast_feature_is_fail_closed_in_worker() -> None:
    unit_of_work, target, _ = build_uow()
    unit_of_work.features.get.return_value.broadcasts = False
    unit_of_work.broadcasts.claim_due_recipients = AsyncMock(return_value=[target])
    service = BroadcastDeliveryService(
        lambda: unit_of_work,
        lease_seconds=120,
        max_attempts=5,  # type: ignore[arg-type]
    )

    assert await service.claim_due("worker", limit=20, now=NOW) == []
    unit_of_work.broadcasts.claim_due_recipients.assert_not_awaited()

    assert await service.prepare_delivery(target.id, "worker") is None
    assert target.status is BroadcastRecipientStatus.SKIPPED
    assert target.last_error == "feature_disabled"
    unit_of_work.users.get_by_id.assert_not_awaited()
