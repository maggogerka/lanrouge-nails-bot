"""Destructive CI-only preservation check for the v0.3.1 -> v0.4 migration."""

from __future__ import annotations

import asyncio
import os
import subprocess
from datetime import UTC, datetime, timedelta

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

V031_REVISION = "20260724_0010"


def _head_revision() -> str:
    revision = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    assert revision is not None
    return revision


def _database_url() -> str:
    return os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL", "")


async def _seed_v031(database_url: str) -> tuple[datetime, datetime]:
    start = datetime(2032, 5, 17, 9, 0, tzinfo=UTC)
    end = start + timedelta(hours=3)
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            assert revision == V031_REVISION
            await connection.execute(
                text(
                    "UPDATE business_settings SET business_name=:name, timezone=:timezone, "
                    "address=:address, map_url=:map_url WHERE id=1"
                ),
                {
                    "name": "Preserved Studio",
                    "timezone": "Europe/Moscow",
                    "address": "Migration test address",
                    "map_url": "https://example.invalid/map",
                },
            )
            await connection.execute(
                text(
                    "UPDATE master_profiles SET display_name=:name, bio=:bio, "
                    "is_published=true WHERE id=1"
                ),
                {"name": "Preserved Master", "bio": "Legacy profile"},
            )
            await connection.execute(
                text(
                    "INSERT INTO users "
                    "(id, telegram_id, username, first_name, phone, role, privacy_consent_at) "
                    "VALUES (71001, 900000001, 'migration_client', 'Legacy', '+79990000001', "
                    "'client', :consented_at)"
                ),
                {"consented_at": start - timedelta(days=30)},
            )
            await connection.execute(
                text(
                    "INSERT INTO services "
                    "(id, name, description, price, duration_min_minutes, "
                    "duration_max_minutes, is_active) VALUES "
                    "(72001, 'Legacy manicure', 'must survive', 3450.00, 120, 180, true)"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO availability_windows "
                    "(id, start_at, end_at, status, admin_comment, created_by) VALUES "
                    "(73001, :start_at, :end_at, 'booked', 'legacy window', 71001)"
                ),
                {"start_at": start, "end_at": end},
            )
            await connection.execute(
                text(
                    "INSERT INTO appointments "
                    "(id, client_id, window_id, service_id, service_name_snapshot, "
                    "price_snapshot, duration_min_snapshot, duration_max_snapshot, status, "
                    "client_comment) VALUES "
                    "(74001, 71001, 73001, 72001, 'Legacy manicure', 3450.00, 120, "
                    "180, 'confirmed', 'preserve me')"
                )
            )
            await connection.execute(
                text(
                    "INSERT INTO consent_history "
                    "(id, user_id, consent_type, previous_value, new_value, source) VALUES "
                    "(75001, 71001, 'privacy', NULL, true, 'onboarding')"
                )
            )
    finally:
        await engine.dispose()
    return start, end


async def _assert_preserved(database_url: str, start: datetime, end: datetime) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
            assert revision == _head_revision()

            business = (
                await connection.execute(
                    text(
                        "SELECT display_name, timezone, address, status::text "
                        "FROM businesses WHERE id=1"
                    )
                )
            ).one()
            assert business == (
                "Preserved Studio",
                "Europe/Moscow",
                "Migration test address",
                "active",
            )

            client = (
                await connection.execute(
                    text(
                        "SELECT u.telegram_id, u.phone, bc.business_id "
                        "FROM users u JOIN business_clients bc ON bc.user_id=u.id "
                        "WHERE u.id=71001"
                    )
                )
            ).one()
            assert client == (900000001, "+79990000001", 1)

            staff = (
                await connection.execute(
                    text(
                        "SELECT display_name, business_id, role::text, is_bookable "
                        "FROM staff_members WHERE id=1"
                    )
                )
            ).one()
            assert staff == ("Preserved Master", 1, "owner", True)

            service = (
                await connection.execute(
                    text(
                        "SELECT name, price, business_id, prepayment_amount, "
                        "online_booking_enabled FROM services WHERE id=72001"
                    )
                )
            ).one()
            assert service.name == "Legacy manicure"
            assert str(service.price) == "3450.00"
            assert service.business_id == 1
            assert str(service.prepayment_amount) == "0.00"
            assert service.online_booking_enabled is True

            appointment = (
                await connection.execute(
                    text(
                        "SELECT business_id, staff_member_id, master_name_snapshot, "
                        "scheduled_start_at, scheduled_end_at, currency_snapshot, "
                        "payment_mode_snapshot::text, client_comment "
                        "FROM appointments WHERE id=74001"
                    )
                )
            ).one()
            assert appointment == (
                1,
                1,
                "Preserved Master",
                start,
                end,
                "RUB",
                "disabled",
                "preserve me",
            )

            consent = (
                await connection.execute(
                    text(
                        "SELECT business_id, policy_version, new_value "
                        "FROM consent_history WHERE id=75001"
                    )
                )
            ).one()
            assert consent == (1, "legacy-unversioned", True)
    finally:
        await engine.dispose()


@pytest.mark.skipif(
    os.environ.get("MIGRATION_PRESERVATION_TEST") != "1",
    reason="destructive migration preservation test is CI-only",
)
def test_seeded_v031_upgrade_preserves_business_data() -> None:
    database_url = _database_url()
    assert database_url, "TEST_DATABASE_URL is required"
    database_name = (make_url(database_url).database or "").casefold()
    assert "test" in database_name or "migration" in database_name

    start, end = asyncio.run(_seed_v031(database_url))
    subprocess.run(["alembic", "upgrade", "head"], check=True)
    asyncio.run(_assert_preserved(database_url, start, end))
