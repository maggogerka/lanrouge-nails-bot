"""Real PostgreSQL isolation, reset and concurrency checks for public demo workspaces."""

from __future__ import annotations

import asyncio
from zoneinfo import ZoneInfo

import pytest

from app.database import Database
from app.demo.service import DemoError, DemoService, DemoStaleAction


def service(database: Database) -> DemoService:
    return DemoService(database.sessions, timezone=ZoneInfo("Europe/Moscow"))


@pytest.mark.asyncio
async def test_two_users_never_see_or_reset_each_others_data(
    integration_database: Database,
) -> None:
    demo = service(integration_database)
    first = await demo.ensure_workspace(101)
    second = await demo.ensure_workspace(202)
    first_service = (await demo.list_services(101))[0]
    second_service = (await demo.list_services(202))[0]
    first_slot = (await demo.list_slots(101, first.generation, first_service.id))[0]
    second_slot = (await demo.list_slots(202, second.generation, second_service.id))[0]

    await demo.book(101, first.generation, first_slot.id)
    await demo.book(202, second.generation, second_slot.id)
    first_appointments = await demo.list_appointments(101)
    second_appointments = await demo.list_appointments(202)
    assert len(first_appointments) == 3
    assert len(second_appointments) == 3
    assert {item.id for item in first_appointments}.isdisjoint(
        {item.id for item in second_appointments}
    )

    reset = await demo.reset(101, first.generation)
    assert len(await demo.list_appointments(101)) == 2
    assert len(await demo.list_appointments(202)) == 3
    with pytest.raises(DemoStaleAction):
        await demo.list_slots(101, first.generation, first_service.id)
    assert reset.generation == first.generation + 1


@pytest.mark.asyncio
async def test_parallel_callbacks_create_only_one_appointment(
    integration_database: Database,
) -> None:
    demo = service(integration_database)
    workspace = await demo.ensure_workspace(303)
    selected_service = (await demo.list_services(303))[0]
    slot = (await demo.list_slots(303, workspace.generation, selected_service.id))[0]

    results = await asyncio.gather(
        demo.book(303, workspace.generation, slot.id),
        demo.book(303, workspace.generation, slot.id),
        return_exceptions=True,
    )

    assert sum(not isinstance(item, Exception) for item in results) == 1
    assert sum(isinstance(item, DemoError) for item in results) == 1
