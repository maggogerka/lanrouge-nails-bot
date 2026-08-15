"""A master cannot use portfolio management callbacks against another master."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.domain.enums import PortfolioStatus, StaffRole
from app.domain.errors import AuthorizationError
from app.schemas.authorization import StaffContext
from app.schemas.service import AdminActor
from app.security.staff_context import staff_authorization_scope
from app.services.portfolio_service import PortfolioService


@pytest.mark.asyncio
async def test_master_cannot_open_another_staff_portfolio_item() -> None:
    unit_of_work = MagicMock()
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    unit_of_work.portfolio.get = AsyncMock(
        return_value=SimpleNamespace(
            id=9,
            staff_member_id=8,
            status=PortfolioStatus.PUBLISHED,
        )
    )
    service = PortfolioService(lambda: unit_of_work, frozenset())  # type: ignore[arg-type]
    context = StaffContext(
        business_id=1,
        staff_member_id=7,
        user_id=70,
        telegram_id=700,
        display_name="Мастер",
        role=StaffRole.MASTER,
        is_bookable=True,
    )

    with staff_authorization_scope(context), pytest.raises(AuthorizationError):
        await service.get_admin(AdminActor(telegram_id=700), 9)
