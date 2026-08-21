"""Deterministic relative-date seed plan for fresh demo workspaces."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class DemoSlotSeed:
    staff_index: int
    service_index: int
    start_at: datetime
    end_at: datetime


def build_slot_seed(now: datetime, timezone: ZoneInfo) -> tuple[DemoSlotSeed, ...]:
    """Create useful windows from now through the next 14 days without fixed dates."""

    aware_now = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    local_now = aware_now.astimezone(timezone)
    result: list[DemoSlotSeed] = []
    for day_offset in range(15):
        day = local_now.date() + timedelta(days=day_offset)
        if day.weekday() == 6:
            continue
        for position, hour in enumerate((10, 13, 16)):
            local_start = datetime.combine(day, time(hour=hour), tzinfo=timezone)
            if local_start <= local_now + timedelta(minutes=45):
                continue
            duration = (60, 90, 60)[position]
            result.append(
                DemoSlotSeed(
                    staff_index=(day_offset + position) % 3,
                    service_index=position % 3,
                    start_at=local_start.astimezone(UTC),
                    end_at=(local_start + timedelta(minutes=duration)).astimezone(UTC),
                )
            )
    return tuple(result)
