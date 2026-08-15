"""Dry-run, retry isolation and idempotency of reference cleanup."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.models import AppointmentReferenceMedia
from app.domain.enums import MediaType
from app.services.reference_cleanup_service import ReferenceCleanupService

NOW = datetime(2026, 7, 24, 9, tzinfo=UTC)


def media(media_id: int, *, expired: bool = True) -> AppointmentReferenceMedia:
    return AppointmentReferenceMedia(
        id=media_id,
        appointment_id=11,
        telegram_file_id=f"file-{media_id}",
        telegram_file_unique_id=f"unique-{media_id}",
        media_type=MediaType.PHOTO,
        position=media_id,
        uploaded_by_user_id=5,
        created_at=NOW - timedelta(days=40),
        expires_at=NOW - timedelta(seconds=1) if expired else NOW + timedelta(days=1),
        deletion_attempts=0,
    )


def build_uow(rows: list[AppointmentReferenceMedia]) -> MagicMock:
    unit_of_work = MagicMock()
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    unit_of_work.reference_media.list_expired = AsyncMock(
        return_value=[row for row in rows if row.expires_at <= NOW and row.deleted_at is None]
    )
    by_id = {row.id: row for row in rows}
    unit_of_work.reference_media.get = AsyncMock(
        side_effect=lambda media_id, **_: by_id.get(media_id)
    )
    unit_of_work.reference_media.get_cleanup_state = AsyncMock(
        return_value=SimpleNamespace(
            last_started_at=None,
            last_succeeded_at=None,
            consecutive_failures=0,
            last_error_code=None,
        )
    )
    unit_of_work.session.flush = AsyncMock()
    unit_of_work.commit = AsyncMock()
    return unit_of_work


@pytest.mark.asyncio
async def test_dry_run_reports_candidates_without_mutation() -> None:
    rows = [media(1), media(2), media(3, expired=False)]
    unit_of_work = build_uow(rows)
    service = ReferenceCleanupService(lambda: unit_of_work)  # type: ignore[arg-type]

    result = await service.run(dry_run=True, now=NOW)

    assert result.checked == 2
    assert result.deleted == 0
    assert result.estimated_bytes_released > 0
    assert all(row.telegram_file_id is not None for row in rows)
    unit_of_work.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_multiple_expired_references_are_anonymized() -> None:
    rows = [media(1), media(2), media(3)]
    unit_of_work = build_uow(rows)
    service = ReferenceCleanupService(lambda: unit_of_work)  # type: ignore[arg-type]

    result = await service.run(dry_run=False, now=NOW)

    assert result.deleted == 3
    assert result.errors == 0
    assert all(row.telegram_file_id is None for row in rows)
    assert all(row.telegram_file_unique_id is None for row in rows)
    assert all(row.deleted_at == NOW for row in rows)


@pytest.mark.asyncio
async def test_second_cleanup_run_is_safe_and_empty() -> None:
    row = media(1)
    unit_of_work = build_uow([row])
    service = ReferenceCleanupService(lambda: unit_of_work)  # type: ignore[arg-type]

    first = await service.run(dry_run=False, now=NOW)
    second = await service.run(dry_run=False, now=NOW)

    assert first.deleted == 1
    assert second.deleted == 0


@pytest.mark.asyncio
async def test_one_item_error_does_not_stop_remaining_items() -> None:
    rows = [media(1), media(2)]
    unit_of_work = build_uow(rows)
    calls_for_one = 0

    async def get_row(media_id: int, **_: object) -> AppointmentReferenceMedia | None:
        nonlocal calls_for_one
        if media_id == 1:
            calls_for_one += 1
            if calls_for_one == 1:
                raise OSError("temporary storage failure")
        return next(row for row in rows if row.id == media_id)

    unit_of_work.reference_media.get = AsyncMock(side_effect=get_row)
    service = ReferenceCleanupService(lambda: unit_of_work)  # type: ignore[arg-type]

    result = await service.run(dry_run=False, now=NOW)

    assert result.errors == 1
    assert result.deleted == 1
    assert rows[0].deletion_attempts == 1
    assert rows[0].last_deletion_error == "temporary_cleanup_error"
    assert rows[1].deleted_at == NOW


@pytest.mark.asyncio
async def test_candidate_query_error_is_recorded_as_failed_cycle() -> None:
    unit_of_work = build_uow([])
    state = await unit_of_work.reference_media.get_cleanup_state()
    unit_of_work.reference_media.list_expired = AsyncMock(side_effect=OSError("temporary"))
    service = ReferenceCleanupService(lambda: unit_of_work)  # type: ignore[arg-type]

    with pytest.raises(OSError, match="temporary"):
        await service.run(dry_run=False, now=NOW)

    assert state.last_started_at == NOW
    assert state.consecutive_failures == 1
    assert state.last_error_code == "cleanup_candidate_query_error"


@pytest.mark.asyncio
async def test_manual_appointment_cleanup_removes_only_selected_rows() -> None:
    rows = [media(1), media(2)]
    unit_of_work = build_uow(rows)
    unit_of_work.reference_media.list_active = AsyncMock(return_value=rows)
    service = ReferenceCleanupService(lambda: unit_of_work)  # type: ignore[arg-type]

    result = await service.cleanup_appointment_now(11, now=NOW)

    assert result.checked == result.deleted == 2
    assert all(row.deleted_at == NOW for row in rows)
