"""Portfolio access, lifecycle, media ordering and publication tests."""

from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.models import PortfolioItem, PortfolioMedia, Service
from app.domain.enums import MediaType, PortfolioDisplayMode, PortfolioStatus
from app.domain.errors import AuthorizationError, PortfolioStateError
from app.schemas.booking import ClientActor
from app.schemas.pagination import PageRequest
from app.schemas.portfolio import (
    PortfolioCreate,
    PortfolioDisplayUpdate,
    PortfolioMediaInput,
)
from app.schemas.service import AdminActor
from app.services.portfolio_service import PortfolioService

NOW = datetime(2026, 7, 22, 18, tzinfo=UTC)


def admin() -> AdminActor:
    return AdminActor(telegram_id=900, first_name="Master")


def values() -> PortfolioCreate:
    return PortfolioCreate(
        title="Красный френч",
        description="Тонкие линии",
        linked_service_id=3,
        design_price=Decimal("500.00"),
        sort_order=2,
        media=[
            PortfolioMediaInput(
                telegram_file_id="file-2",
                telegram_file_unique_id="unique-2",
                media_type=MediaType.PHOTO,
            ),
            PortfolioMediaInput(
                telegram_file_id="file-1",
                telegram_file_unique_id="unique-1",
                media_type=MediaType.PHOTO,
            ),
        ],
        tag_names=[],
    )


def build_uow() -> MagicMock:
    unit_of_work = MagicMock()
    unit_of_work.__aenter__ = AsyncMock(return_value=unit_of_work)
    unit_of_work.__aexit__ = AsyncMock(return_value=None)
    unit_of_work.users.get_or_create_admin = AsyncMock(return_value=SimpleNamespace(id=9))
    unit_of_work.settings.get = AsyncMock(
        return_value=SimpleNamespace(
            portfolio_max_media=8,
            portfolio_enabled=True,
            portfolio_mode=PortfolioDisplayMode.INTERNAL,
            external_portfolio_url=None,
            external_portfolio_button_text="Открыть портфолио",
            version=1,
        )
    )
    unit_of_work.services.get = AsyncMock(
        return_value=Service(
            id=3,
            name="Маникюр",
            price=Decimal("2500.00"),
            duration_min_minutes=120,
            duration_max_minutes=180,
            is_active=True,
        )
    )
    saved_media: list[PortfolioMedia] = []

    async def add_item(item: PortfolioItem) -> PortfolioItem:
        item.id = 11
        return item

    async def add_media(media: list[PortfolioMedia]) -> None:
        for index, item in enumerate(media, start=1):
            item.id = index
        saved_media.extend(media)

    unit_of_work.portfolio.add = AsyncMock(side_effect=add_item)
    unit_of_work.portfolio.add_media = AsyncMock(side_effect=add_media)
    unit_of_work.portfolio.list_media = AsyncMock(side_effect=lambda _item_id: saved_media)
    unit_of_work.portfolio.list_item_tags = AsyncMock(return_value=[])
    unit_of_work.audit.add = AsyncMock()
    unit_of_work.session.flush = AsyncMock()
    unit_of_work.commit = AsyncMock()
    return unit_of_work


@pytest.mark.asyncio
async def test_admin_can_create_draft_and_media_order_is_preserved() -> None:
    unit_of_work = build_uow()
    service = PortfolioService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    result = await service.create(admin(), values(), publish=False, now=NOW)

    assert result.status is PortfolioStatus.DRAFT
    assert [media.telegram_file_id for media in result.media] == ["file-2", "file-1"]
    assert [media.position for media in result.media] == [0, 1]
    unit_of_work.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_admin_cannot_create_portfolio_work() -> None:
    factory = MagicMock()
    service = PortfolioService(factory, frozenset({900}))

    with pytest.raises(AuthorizationError):
        await service.create(
            AdminActor(telegram_id=901),
            values(),
            publish=False,
            now=NOW,
        )

    factory.assert_not_called()


@pytest.mark.asyncio
async def test_client_query_is_always_filtered_to_published() -> None:
    unit_of_work = build_uow()
    unit_of_work.portfolio.list_page = AsyncMock(return_value=([], 0))
    service = PortfolioService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    page = await service.list_published(
        ClientActor(telegram_id=101),
        PageRequest(page=1, page_size=5),
    )

    assert page.items == []
    assert unit_of_work.portfolio.list_page.await_args.kwargs["status"] is PortfolioStatus.PUBLISHED


@pytest.mark.asyncio
async def test_archived_items_are_not_returned_by_client_query() -> None:
    unit_of_work = build_uow()
    archived = PortfolioItem(
        id=12,
        title="Архив",
        status=PortfolioStatus.ARCHIVED,
        sort_order=0,
        created_by=9,
    )

    async def list_page(**kwargs: object) -> tuple[list[PortfolioItem], int]:
        return ([], 0) if kwargs["status"] is PortfolioStatus.PUBLISHED else ([archived], 1)

    unit_of_work.portfolio.list_page = AsyncMock(side_effect=list_page)
    service = PortfolioService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    page = await service.list_published(
        ClientActor(telegram_id=101),
        PageRequest(page=1, page_size=5),
    )

    assert not page.items


@pytest.mark.asyncio
async def test_external_and_disabled_modes_block_internal_queries_without_deleting_items() -> None:
    unit_of_work = build_uow()
    unit_of_work.portfolio.list_page = AsyncMock(return_value=([], 0))
    service = PortfolioService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    for mode in (PortfolioDisplayMode.EXTERNAL_LINK, PortfolioDisplayMode.DISABLED):
        unit_of_work.settings.get.return_value.portfolio_mode = mode
        with pytest.raises(PortfolioStateError, match="недоступно"):
            await service.list_published(ClientActor(telegram_id=101), PageRequest())

    unit_of_work.portfolio.list_page.assert_not_awaited()


@pytest.mark.asyncio
async def test_external_mode_requires_https_url_and_preserves_internal_content() -> None:
    unit_of_work = build_uow()
    service = PortfolioService(lambda: unit_of_work, frozenset({900}))  # type: ignore[arg-type]

    with pytest.raises(PortfolioStateError, match="ссылку"):
        await service.update_display_config(
            admin(), PortfolioDisplayUpdate(mode=PortfolioDisplayMode.EXTERNAL_LINK)
        )
    with pytest.raises(ValueError, match="HTTPS"):
        PortfolioDisplayUpdate(external_url="http://example.com/works")

    await service.update_display_config(
        admin(),
        PortfolioDisplayUpdate(external_url="https://example.com/works"),
    )
    config = await service.update_display_config(
        admin(),
        PortfolioDisplayUpdate(mode=PortfolioDisplayMode.EXTERNAL_LINK),
        correlation_id="portfolio-mode",
    )

    assert config.mode is PortfolioDisplayMode.EXTERNAL_LINK
    assert config.external_url == "https://example.com/works"
    assert unit_of_work.settings.get.return_value.portfolio_enabled
    unit_of_work.portfolio.delete_media.assert_not_called()
    assert unit_of_work.audit.add.await_args.kwargs["action"] == "portfolio.display_changed"
