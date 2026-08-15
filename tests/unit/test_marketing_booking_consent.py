"""Broadcast booking callbacks obey current privacy consent independently of opt-out."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiogram.types import CallbackQuery, Message

from app.domain.enums import BusinessType, MarketingEventType
from app.handlers.client.marketing import marketing_booking_click
from app.handlers.client.onboarding import accept_privacy
from app.schemas.booking import ConsentStatus
from app.schemas.presentation import BusinessPresentation
from app.states.booking import PENDING_MARKETING_BOOKING_KEY


def _callback() -> MagicMock:
    callback = MagicMock(spec=CallbackQuery)
    callback.from_user = SimpleNamespace(
        id=777,
        username="client",
        first_name="Анна",
        last_name=None,
    )
    callback.message = MagicMock(spec=Message)
    callback.message.answer = AsyncMock()
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()
    return callback


def _presentation() -> BusinessPresentation:
    return BusinessPresentation(
        business_id=1,
        display_name="Студия",
        business_type=BusinessType.SOLO,
        timezone="Europe/Moscow",
        currency="RUB",
        privacy_policy_url="https://example.test/privacy",
    )


@pytest.mark.asyncio
async def test_broadcast_booking_pauses_for_current_privacy_consent() -> None:
    callback = _callback()
    state = MagicMock()
    state.update_data = AsyncMock()
    consent = MagicMock()
    consent.get_or_create_status = AsyncMock(
        return_value=ConsentStatus(
            privacy_accepted=False,
            marketing_answered=True,
            marketing_accepted=False,
        )
    )
    events = MagicMock()
    events.record = AsyncMock()
    presentation = MagicMock()
    presentation.get_business = AsyncMock(return_value=_presentation())

    await marketing_booking_click(
        callback,
        SimpleNamespace(action="book", broadcast_id=41),
        state,
        events,
        MagicMock(),
        consent,
        presentation,
    )

    state.update_data.assert_awaited_once_with(
        {
            PENDING_MARKETING_BOOKING_KEY: {
                "broadcast_id": 41,
                "event_type": MarketingEventType.BOOKING_CLICKED.value,
            }
        }
    )
    events.record.assert_not_awaited()
    assert "рекламной подписки" in callback.message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_marketing_opt_out_does_not_block_privacy_accepted_booking() -> None:
    callback = _callback()
    state = MagicMock()
    consent = MagicMock()
    consent.get_or_create_status = AsyncMock(
        return_value=ConsentStatus(
            privacy_accepted=True,
            marketing_answered=True,
            marketing_accepted=False,
        )
    )
    events = MagicMock()
    events.record = AsyncMock()

    with patch("app.handlers.client.marketing.start_booking", new=AsyncMock()) as start:
        await marketing_booking_click(
            callback,
            SimpleNamespace(action="book", broadcast_id=42),
            state,
            events,
            MagicMock(),
            consent,
            MagicMock(),
        )

    actor = events.record.await_args.args[0]
    assert actor.telegram_id == 777
    start.assert_awaited_once()
    assert start.await_args.kwargs["actor"].telegram_id == 777


@pytest.mark.asyncio
async def test_accepting_privacy_resumes_original_broadcast_booking() -> None:
    callback = _callback()
    pending = {
        PENDING_MARKETING_BOOKING_KEY: {
            "broadcast_id": 43,
            "event_type": MarketingEventType.WINDOWS_CLICKED.value,
        }
    }
    state = MagicMock()
    state.get_data = AsyncMock(return_value=pending)
    state.set_data = AsyncMock()
    consent = MagicMock()
    consent.accept_privacy = AsyncMock()
    events = MagicMock()
    events.record = AsyncMock()

    with patch("app.handlers.client.onboarding.start_booking", new=AsyncMock()) as start:
        await accept_privacy(
            callback,
            state,
            consent,
            MagicMock(),
            events,
            MagicMock(),
            "corr-resume",
        )

    events.record.assert_awaited_once()
    assert events.record.await_args.args[1:] == (43, MarketingEventType.WINDOWS_CLICKED)
    start.assert_awaited_once()
    callback.message.answer.assert_not_awaited()
