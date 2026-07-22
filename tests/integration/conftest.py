"""Dedicated PostgreSQL fixtures guarded against non-test databases."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.database import Database


@pytest_asyncio.fixture
async def integration_database() -> AsyncIterator[Database]:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    database_name = database_url.rsplit("/", maxsplit=1)[-1].split("?", maxsplit=1)[0]
    if "test" not in database_name.casefold():
        raise RuntimeError("Integration tests require a database name containing 'test'")

    database = Database.create(database_url)
    async with database.engine.begin() as connection:
        await connection.execute(
            text(
                "TRUNCATE TABLE audit_logs, notification_jobs, appointment_reference_media, "
                "waitlist_notifications, waitlist_entries, review_revisions, reviews, "
                "broadcast_recipients, broadcast_media, broadcasts, marketing_events, "
                "master_public_links, master_profiles, "
                "portfolio_item_tags, portfolio_media, portfolio_items, portfolio_tags, "
                "user_client_tags, client_notes, client_tags, consent_history, "
                "appointment_status_history, appointments, availability_windows, "
                "services, users RESTART IDENTITY CASCADE"
            )
        )
    try:
        yield database
    finally:
        await database.close()
