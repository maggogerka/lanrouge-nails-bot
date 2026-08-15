"""Onboarding renders the same versioned copy whose digest is persisted."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.enums import BusinessType, StaffRole
from app.domain.legal import MARKETING_CONSENT_TEXT
from app.handlers.client.onboarding import handle_start
from app.schemas.booking import ConsentStatus
from app.schemas.presentation import BusinessPresentation


@pytest.mark.asyncio
async def test_onboarding_uses_versioned_marketing_consent_text() -> None:
    message = MagicMock()
    message.from_user = SimpleNamespace(
        id=101,
        username="client",
        first_name="Анна",
        last_name=None,
    )
    message.answer = AsyncMock()
    state = MagicMock()
    state.clear = AsyncMock()
    state.update_data = AsyncMock()
    state.get_data = AsyncMock(return_value={})
    state.set_data = AsyncMock()
    consent_service = MagicMock()
    consent_service.get_or_create_status = AsyncMock(
        return_value=ConsentStatus(
            privacy_accepted=True,
            marketing_answered=False,
            marketing_accepted=False,
        )
    )
    presentation_service = MagicMock()
    presentation_service.get_business = AsyncMock(
        return_value=BusinessPresentation(
            business_id=1,
            display_name="Новая студия",
            business_type=BusinessType.SOLO,
            timezone="Europe/Moscow",
            currency="RUB",
            privacy_policy_url="https://example.test/privacy",
        )
    )

    await handle_start(
        message,
        state,
        consent_service,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        presentation_service,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        "corr-onboarding",
    )

    rendered_text = message.answer.await_args.args[0]
    assert rendered_text == MARKETING_CONSENT_TEXT
    assert "reply_markup" in message.answer.await_args.kwargs


@pytest.mark.asyncio
async def test_staff_deep_link_is_consumed_before_client_consent_flow() -> None:
    message = MagicMock()
    message.text = f"/start staff_{'A' * 43}"
    message.from_user = SimpleNamespace(
        id=202,
        username="master",
        first_name="Мария",
        last_name=None,
    )
    message.answer = AsyncMock()
    state = MagicMock()
    state.clear = AsyncMock()
    authorization = MagicMock()
    authorization.accept_invitation = AsyncMock(
        return_value=SimpleNamespace(staff=SimpleNamespace(role=StaffRole.MASTER))
    )
    presentation = MagicMock()
    presentation.get_business = AsyncMock()

    await handle_start(
        message,
        state,
        MagicMock(),
        MagicMock(),
        MagicMock(),
        MagicMock(),
        presentation,
        MagicMock(),
        MagicMock(),
        authorization,
        "corr-staff",
    )

    authorization.accept_invitation.assert_awaited_once()
    assert authorization.accept_invitation.await_args.kwargs["correlation_id"] == "corr-staff"
    presentation.get_business.assert_not_awaited()
    assert "/master" in message.answer.await_args.args[0]
