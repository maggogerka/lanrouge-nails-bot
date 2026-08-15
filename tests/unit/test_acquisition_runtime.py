"""Consent-gated runtime acquisition attribution tests."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.domain.enums import ConsentType
from app.handlers.client.onboarding import _campaign_code
from app.schemas.booking import ClientActor
from app.services.acquisition_service import AcquisitionRuntimeService

NOW = datetime(2026, 8, 10, 12, tzinfo=UTC)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ("avito", "avito"),
        ("VK", "vk"),
        ("source_qr-studio", "qr-studio"),
        ("portfolio_42", None),
        ("client phone +79990000000", None),
        (None, None),
    ],
)
def test_start_payload_accepts_safe_campaign_codes_without_breaking_portfolio(
    payload: str | None,
    expected: str | None,
) -> None:
    assert _campaign_code(payload) == expected


class FakePrivacyRepository:
    business_id = 1

    def __init__(self, *, consent_version: str = "privacy-v2", source_exists: bool = True) -> None:
        self.consent_version = consent_version
        self.source_exists = source_exists
        self.added_attribution = None
        self.get_source_by_code_calls = 0

    async def get_business(self) -> object:
        return SimpleNamespace(
            privacy_policy_url="https://example.test/privacy",
            privacy_policy_hash=None,
            privacy_policy_version="privacy-v2",
        )

    async def latest_consent(self, user_id: int, consent_type: ConsentType) -> object:
        assert user_id == 10
        assert consent_type is ConsentType.PRIVACY
        return SimpleNamespace(
            new_value=True,
            policy_version=self.consent_version,
            policy_url="https://example.test/privacy",
            policy_hash=None,
        )

    async def get_client_by_user(self, user_id: int, *, for_update: bool) -> object:
        assert user_id == 10
        assert for_update
        return SimpleNamespace(id=20)

    async def get_client(self, client_id: int, *, for_update: bool) -> object:
        assert client_id == 20
        assert for_update
        return SimpleNamespace(id=20)

    async def get_source_by_code(self, code: str) -> object | None:
        self.get_source_by_code_calls += 1
        assert code in {"avito", "unknown"}
        return SimpleNamespace(id=30) if self.source_exists else None

    async def get_attribution(self, client_id: int, *, for_update: bool) -> None:
        assert client_id == 20
        assert for_update
        return None

    async def add_attribution(self, attribution: Any) -> object:
        attribution.id = 40
        self.added_attribution = attribution
        return attribution

    async def flush(self) -> None:
        return None


class FakeUnitOfWork:
    business_id = 1

    def __init__(self, privacy: FakePrivacyRepository) -> None:
        self.privacy = privacy
        self.users = SimpleNamespace(
            get_or_create_client=AsyncMock(return_value=SimpleNamespace(id=10))
        )
        self.audit = SimpleNamespace(add=AsyncMock())
        self.commit = AsyncMock()

    async def __aenter__(self) -> FakeUnitOfWork:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


def actor() -> ClientActor:
    return ClientActor(
        telegram_id=101,
        username="client",
        first_name="Client",
        last_name=None,
    )


@pytest.mark.asyncio
async def test_known_source_is_recorded_only_with_current_privacy_consent() -> None:
    privacy = FakePrivacyRepository()
    unit_of_work = FakeUnitOfWork(privacy)
    service = AcquisitionRuntimeService(lambda: unit_of_work)  # type: ignore[arg-type]

    recorded = await service.record_known_touch(
        actor(),
        raw_code="AVITO",
        correlation_id="corr-1",
        touched_at=NOW,
    )

    assert recorded
    assert privacy.added_attribution is not None
    assert privacy.added_attribution.first_source_id == 30
    unit_of_work.audit.add.assert_awaited_once()
    unit_of_work.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_stale_privacy_policy_prevents_personal_attribution() -> None:
    privacy = FakePrivacyRepository(consent_version="privacy-v1")
    unit_of_work = FakeUnitOfWork(privacy)
    service = AcquisitionRuntimeService(lambda: unit_of_work)  # type: ignore[arg-type]

    recorded = await service.record_known_touch(actor(), raw_code="avito", touched_at=NOW)

    assert not recorded
    assert privacy.get_source_by_code_calls == 0
    assert privacy.added_attribution is None
    unit_of_work.audit.add.assert_not_awaited()
    unit_of_work.commit.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("raw_code", ["unknown", "invalid code"])
async def test_unknown_and_invalid_campaigns_have_same_non_enumerating_result(
    raw_code: str,
) -> None:
    privacy = FakePrivacyRepository(source_exists=False)
    unit_of_work = FakeUnitOfWork(privacy)
    service = AcquisitionRuntimeService(lambda: unit_of_work)  # type: ignore[arg-type]

    assert not await service.record_known_touch(actor(), raw_code=raw_code, touched_at=NOW)
    unit_of_work.audit.add.assert_not_awaited()
    unit_of_work.commit.assert_not_awaited()
