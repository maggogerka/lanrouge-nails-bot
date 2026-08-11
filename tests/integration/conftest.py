"""Dedicated PostgreSQL fixtures guarded against non-test databases."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import pytest
import pytest_asyncio
from sqlalchemy import text

from app.database import Database


@pytest_asyncio.fixture
async def integration_database() -> AsyncIterator[Database]:
    database_url = os.getenv("TEST_DATABASE_URL")
    if not database_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    password_file = os.getenv("TEST_DATABASE_PASSWORD_FILE")
    if password_file:
        password = Path(password_file).read_text(encoding="utf-8").strip()
        parsed = urlsplit(database_url)
        if parsed.hostname is None or parsed.username is None:
            raise RuntimeError("TEST_DATABASE_URL must include a username and hostname")
        host = parsed.hostname
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        netloc = f"{parsed.username}:{quote(password, safe='')}@{host}"
        database_url = urlunsplit(
            (parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment)
        )
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
