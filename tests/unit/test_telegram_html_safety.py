"""Untrusted business/client text must not become Telegram HTML."""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.types import Message

from app.domain.enums import WaitlistStatus
from app.handlers.admin.waitlist import _render_entry
from app.handlers.client.master_profile import show_master_profile
from app.handlers.client.waitlist import _render_waitlist_line
from app.schemas.master_profile import MasterProfileView
from app.schemas.waitlist import AdminWaitlistView, WaitlistDelivery, WaitlistView
from app.workers.reminders import render_waitlist_offer

NOW = datetime(2026, 8, 15, 12, tzinfo=UTC)


def waitlist_view() -> WaitlistView:
    return WaitlistView(
        id=1,
        service_id=2,
        service_name='Маникюр <vip> & "spa" ✨',
        date_from=date(2026, 8, 20),
        date_to=date(2026, 8, 21),
        preferred_dates=[],
        preferred_time_from=None,
        preferred_time_to=None,
        status=WaitlistStatus.ACTIVE,
        expires_at=NOW,
    )


def test_waitlist_client_and_admin_views_escape_dynamic_html() -> None:
    client = waitlist_view()
    admin = AdminWaitlistView(
        **client.model_dump(),
        client_id=3,
        client_name="Анна <script> & Co",
        client_telegram_id=123,
    )

    client_text = _render_waitlist_line(client)
    admin_text = _render_entry(admin)

    assert "Маникюр &lt;vip&gt; &amp; &quot;spa&quot; ✨" in client_text
    assert "Анна &lt;script&gt; &amp; Co" in admin_text
    assert "<script>" not in admin_text


def test_waitlist_worker_escapes_service_name() -> None:
    delivery = WaitlistDelivery(
        notification_id=1,
        entry_id=2,
        window_id=3,
        recipient_user_id=4,
        recipient_telegram_id=5,
        service_name="Услуга <b> & ✨",
        start_at=NOW,
        timezone="Europe/Moscow",
        attempts=1,
    )

    text = render_waitlist_offer(delivery)

    assert "Услуга &lt;b&gt; &amp; ✨" in text
    assert "Услуга <b>" not in text


@pytest.mark.asyncio
async def test_schema_maximum_master_profile_is_escaped_and_safely_split() -> None:
    profile = MasterProfileView(
        id=1,
        display_name="<&😀" + "М" * 251,
        bio="<&😀x" * 1000,
        telegram_photo_file_id="master-photo",
        telegram_photo_file_unique_id="unique",
        address="<&😀x" * 125,
        map_url=None,
        telegram_url=None,
        is_published=True,
        links=[],
    )
    service = SimpleNamespace(get_public=AsyncMock(return_value=profile))
    message = MagicMock(spec=Message)
    message.answer_photo = AsyncMock(return_value=MagicMock(spec=Message))
    message.answer = AsyncMock(return_value=MagicMock(spec=Message))

    await show_master_profile(message, service)

    message.answer_photo.assert_awaited_once_with("master-photo")
    assert message.answer.await_count >= 2
    combined = "".join(call.args[0] for call in message.answer.await_args_list)
    assert "<script>" not in combined
    assert "&lt;&amp;😀x" in combined
