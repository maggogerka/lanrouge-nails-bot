"""Broadcast authorization, audience snapshots, confirmation and cancellation."""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.models import Broadcast, BroadcastMedia
from app.domain.enums import (
    BroadcastAudienceType,
    BroadcastButtonType,
    BroadcastRecipientStatus,
    BroadcastStatus,
)
from app.domain.errors import AuthorizationError, BroadcastStateError
from app.schemas.broadcast import BroadcastCreate
from app.schemas.service import AdminActor
from app.services.broadcast_service import BroadcastService

NOW = datetime(2026, 7, 22, 9, tzinfo=UTC)


def values() -> BroadcastCreate:
    return BroadcastCreate(
        title="Новые дизайны",
        text="Посмотрите новые работы <без HTML>",
        audience_type=BroadcastAudienceType.ALL_SUBSCRIBED,
        button_type=BroadcastButtonType.PORTFOLIO,
        button_text="Посмотреть работы",
    )


def test_broadcast_schema_rejects_text_over_telegram_utf16_limit() -> None:
    with pytest.raises(ValueError, match="4096"):
        BroadcastCreate(
            title="Emoji overflow",
            text="x" * 4095 + "😀",
        )


def build_uow() -> MagicMock:
    unit_of_work = MagicMock()
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    unit_of_work.users.get_or_create_admin = AsyncMock(return_value=SimpleNamespace(id=9))
    unit_of_work.settings.get = AsyncMock(
        return_value=SimpleNamespace(broadcasts_enabled=True, broadcast_max_media=5)
    )
    saved_media: list[BroadcastMedia] = []

    async def save(broadcast: Broadcast) -> Broadcast:
        broadcast.id = 31
        broadcast.created_at = NOW
        broadcast.updated_at = NOW
        return broadcast

    async def add_media(media: list[BroadcastMedia]) -> None:
        saved_media.extend(media)

    unit_of_work.broadcasts.add = AsyncMock(side_effect=save)
    unit_of_work.broadcasts.add_media = AsyncMock(side_effect=add_media)
    unit_of_work.broadcasts.list_media = AsyncMock(return_value=saved_media)
    unit_of_work.broadcasts.resolve_audience_user_ids = AsyncMock(return_value=[5, 6])
    unit_of_work.broadcasts.freeze_recipients = AsyncMock(return_value=2)
    unit_of_work.broadcasts.status_counts = AsyncMock(
        return_value={BroadcastRecipientStatus.PENDING: 2}
    )
    unit_of_work.broadcasts.cancel_open_recipients = AsyncMock(return_value=2)
    unit_of_work.audit.add = AsyncMock()
    unit_of_work.commit = AsyncMock()
    return unit_of_work


@pytest.mark.asyncio
async def test_non_admin_cannot_create_broadcast() -> None:
    service = BroadcastService(MagicMock(), frozenset({900}))
    with pytest.raises(AuthorizationError):
        await service.create_draft(AdminActor(telegram_id=901), values())


@pytest.mark.asyncio
async def test_draft_uses_plain_text_and_launch_freezes_audience_once() -> None:
    unit_of_work = build_uow()
    service = BroadcastService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]
    draft = await service.create_draft(AdminActor(telegram_id=900), values())
    target = unit_of_work.broadcasts.add.await_args.args[0]
    unit_of_work.broadcasts.get = AsyncMock(return_value=target)

    assert draft.text.endswith("<без HTML>")
    assert target.parse_mode is None
    result = await service.launch(AdminActor(telegram_id=900), draft.id, confirmed=True, now=NOW)

    assert result.total == 2
    assert target.status is BroadcastStatus.SCHEDULED
    unit_of_work.broadcasts.freeze_recipients.assert_awaited_once_with(
        broadcast_id=31, user_ids=[5, 6], scheduled_at=NOW
    )


@pytest.mark.asyncio
async def test_mass_launch_without_confirmation_is_impossible() -> None:
    service = BroadcastService(MagicMock(), frozenset({900}))
    with pytest.raises(BroadcastStateError, match="подтверждение"):
        await service.launch(AdminActor(telegram_id=900), 31, confirmed=False, now=NOW)


@pytest.mark.asyncio
async def test_cancellation_stops_pending_and_preserves_counts() -> None:
    unit_of_work = build_uow()
    target = Broadcast(
        id=31,
        title="Campaign",
        text="Text",
        status=BroadcastStatus.SENDING,
        audience_type=BroadcastAudienceType.ALL_SUBSCRIBED,
        button_type=BroadcastButtonType.NONE,
        audience_parameters={},
        created_by=9,
        created_at=NOW,
        updated_at=NOW,
    )
    unit_of_work.broadcasts.get = AsyncMock(return_value=target)
    unit_of_work.broadcasts.status_counts.return_value = {
        BroadcastRecipientStatus.SENT: 3,
        BroadcastRecipientStatus.SKIPPED: 2,
    }
    service = BroadcastService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    result = await service.cancel(AdminActor(telegram_id=900), 31)

    assert target.status is BroadcastStatus.CANCELLED
    assert result.total == 5
    unit_of_work.broadcasts.cancel_open_recipients.assert_awaited_once_with(31)
