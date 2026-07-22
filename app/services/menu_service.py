"""Resolve menu visibility from one consistent database snapshot."""

from __future__ import annotations

from collections.abc import Callable

from app.domain.enums import PortfolioDisplayMode
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.menu import MenuCapabilities

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


class MenuService:
    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    async def get_capabilities(self) -> MenuCapabilities:
        async with self._unit_of_work_factory() as unit_of_work:
            settings = await unit_of_work.settings.get()
            profile = await unit_of_work.master_profile.get()
            if settings is None:
                raise RuntimeError("Business settings row is missing")
            mode = PortfolioDisplayMode(
                getattr(settings, "portfolio_mode", PortfolioDisplayMode.INTERNAL)
            )
            return MenuCapabilities(
                portfolio_visible=mode is not PortfolioDisplayMode.DISABLED,
                reviews_visible=bool(settings.reviews_enabled),
                master_profile_visible=bool(
                    settings.master_profile_enabled and profile is not None and profile.is_published
                ),
            )
