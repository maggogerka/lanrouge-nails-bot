"""Campaign-code and first/last-touch projection tests."""

from datetime import UTC, datetime, timedelta

import pytest

from app.domain.acquisition import (
    AttributionProjection,
    CampaignValidationError,
    validate_campaign_code,
)

NOW = datetime(2026, 8, 10, 9, tzinfo=UTC)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("avito", "avito"),
        (" VK_Ads-01 ", "vk_ads-01"),
        ("qr", "qr"),
        ("referral", "referral"),
    ],
)
def test_campaign_codes_are_normalized(raw: str, expected: str) -> None:
    assert validate_campaign_code(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["", "has space", "client@example.test", "кириллица", "x" * 65, "?source=vk"],
)
def test_campaign_codes_reject_free_form_or_pii_shaped_payloads(raw: str) -> None:
    with pytest.raises(CampaignValidationError):
        validate_campaign_code(raw)


def test_first_touch_is_immutable_and_last_touch_advances() -> None:
    first = AttributionProjection.first(source_id=10, touched_at=NOW)

    updated = first.touch(source_id=20, touched_at=NOW + timedelta(days=1))

    assert updated.first_source_id == 10
    assert updated.first_touched_at == NOW
    assert updated.last_source_id == 20
    assert updated.touch_count == 2


def test_out_of_order_touch_is_rejected() -> None:
    first = AttributionProjection.first(source_id=10, touched_at=NOW)

    with pytest.raises(CampaignValidationError):
        first.touch(source_id=20, touched_at=NOW - timedelta(seconds=1))


def test_naive_touch_timestamp_is_rejected() -> None:
    with pytest.raises(CampaignValidationError, match="timezone-aware"):
        AttributionProjection.first(
            source_id=10,
            touched_at=datetime(2026, 8, 10, 9),
        )
