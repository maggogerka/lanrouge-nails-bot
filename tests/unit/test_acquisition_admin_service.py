"""Acquisition administration exposes only aggregate tenant-scoped data."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.models.privacy import AcquisitionSource
from app.domain.enums import StaffRole
from app.repositories.privacy_repository import AcquisitionMetricRow
from app.schemas.acquisition import AcquisitionSourceView
from app.schemas.authorization import StaffContext, StaffPermission
from app.services.acquisition_admin_service import AcquisitionAdministrationService


def actor() -> StaffContext:
    return StaffContext(
        business_id=1,
        staff_member_id=4,
        user_id=8,
        telegram_id=123,
        display_name="Owner",
        role=StaffRole.OWNER,
        is_bookable=True,
    )


def source() -> AcquisitionSource:
    return AcquisitionSource(
        id=3,
        business_id=1,
        code="avito",
        display_name="Avito",
        channel="classifieds",
        is_active=True,
    )


def build_service() -> tuple[AcquisitionAdministrationService, MagicMock, MagicMock]:
    authorization = MagicMock()
    authorization.authorize = AsyncMock(return_value=actor())
    unit_of_work = MagicMock()
    unit_of_work.business_id = 1
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    unit_of_work.commit = AsyncMock()
    unit_of_work.audit.add = AsyncMock()
    return (
        AcquisitionAdministrationService(
            lambda: unit_of_work,  # type: ignore[arg-type]
            authorization,
        ),
        authorization,
        unit_of_work,
    )


@pytest.mark.asyncio
async def test_statistics_are_aggregate_and_live_authorized() -> None:
    service, authorization, unit_of_work = build_service()
    unit_of_work.privacy.acquisition_metrics = AsyncMock(
        return_value=(
            AcquisitionMetricRow(
                source=source(),
                clients_arrived=12,
                clients_started_booking=8,
                clients_completed_booking=6,
                repeat_clients=2,
            ),
        )
    )

    result = await service.list_metrics(actor())

    assert result[0].clients_arrived == 12
    assert result[0].repeat_clients == 2
    assert not hasattr(result[0], "telegram_id")
    authorization.authorize.assert_awaited_once_with(
        business_id=1,
        telegram_id=123,
        permission=StaffPermission.VIEW_ALL_STATISTICS,
    )


def test_campaign_link_contains_only_public_bot_name_and_validated_code() -> None:
    view = AcquisitionSourceView.model_validate(source())

    link = AcquisitionAdministrationService.link_for(view, "@example_nails_bot")

    assert link.deep_link == "https://t.me/example_nails_bot?start=avito"
    assert link.qr_payload == link.deep_link
    with pytest.raises(ValueError):
        AcquisitionAdministrationService.link_for(view, "bad host/name")
