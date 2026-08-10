"""CRM subscription state remains separate from client payment flows."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.enums import SubscriptionProvider, SubscriptionStatus
from app.schemas.subscription import SubscriptionView
from app.services.subscription_service import SubscriptionAccessError, SubscriptionService

NOW = datetime(2026, 8, 11, 12, tzinfo=UTC)


def subscription(
    status: SubscriptionStatus,
    *,
    paid_until: datetime | None = None,
    grace_ends_at: datetime | None = None,
) -> SubscriptionView:
    return SubscriptionView(
        business_id=1,
        plan_code="standard",
        provider=SubscriptionProvider.MANUAL,
        status=status,
        paid_until=paid_until,
        grace_ends_at=grace_ends_at,
        feature_limits={"active_masters": 5},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "view",
    [
        subscription(SubscriptionStatus.TRIAL, paid_until=NOW + timedelta(days=1)),
        subscription(SubscriptionStatus.ACTIVE, paid_until=NOW + timedelta(days=30)),
        subscription(
            SubscriptionStatus.PAST_DUE,
            paid_until=NOW - timedelta(days=1),
            grace_ends_at=NOW + timedelta(days=3),
        ),
    ],
)
async def test_active_and_grace_period_states_allow_new_bookings(view: SubscriptionView) -> None:
    provider = MagicMock()
    provider.get_status = AsyncMock(return_value=view)
    service = SubscriptionService(provider)

    assert await service.ensure_new_bookings_allowed(1, now=NOW) == view


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "view",
    [
        subscription(SubscriptionStatus.SUSPENDED),
        subscription(SubscriptionStatus.CANCELLED),
        subscription(
            SubscriptionStatus.PAST_DUE,
            paid_until=NOW - timedelta(days=10),
            grace_ends_at=NOW - timedelta(seconds=1),
        ),
    ],
)
async def test_terminal_or_expired_grace_state_blocks_only_new_bookings(
    view: SubscriptionView,
) -> None:
    provider = MagicMock()
    provider.get_status = AsyncMock(return_value=view)
    service = SubscriptionService(provider)

    with pytest.raises(SubscriptionAccessError):
        await service.ensure_new_bookings_allowed(1, now=NOW)


def test_owner_warning_is_due_before_expiry_or_during_past_due() -> None:
    assert SubscriptionService.owner_warning_due(
        subscription(SubscriptionStatus.ACTIVE, paid_until=NOW + timedelta(days=7)),
        now=NOW,
    )
    assert SubscriptionService.owner_warning_due(
        subscription(SubscriptionStatus.PAST_DUE, grace_ends_at=NOW + timedelta(days=20)),
        now=NOW,
    )
    assert not SubscriptionService.owner_warning_due(
        subscription(SubscriptionStatus.ACTIVE, paid_until=NOW + timedelta(days=8)),
        now=NOW,
    )
