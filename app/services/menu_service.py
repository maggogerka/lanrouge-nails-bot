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
            flags = await unit_of_work.features.get()
            profile = await unit_of_work.master_profile.get()
            if settings is None:
                raise RuntimeError("Business settings row is missing")
            if flags is None:
                raise RuntimeError("Business feature settings are missing")
            mode = PortfolioDisplayMode(
                getattr(settings, "portfolio_mode", PortfolioDisplayMode.INTERNAL)
            )
            return MenuCapabilities(
                online_booking_visible=bool(flags.online_booking),
                masters_visible=bool(flags.master_selection),
                portfolio_visible=bool(flags.portfolio)
                and mode is not PortfolioDisplayMode.DISABLED,
                reviews_visible=bool(flags.reviews) and bool(settings.reviews_enabled),
                notifications_visible=bool(flags.reminders or flags.repeat_booking),
                repeat_booking_visible=bool(flags.repeat_booking),
                waitlist_visible=bool(flags.waitlist) and bool(settings.waitlist_enabled),
                support_visible=bool(flags.client_support),
                broadcasts_visible=bool(flags.broadcasts),
                master_profile_visible=bool(
                    settings.master_profile_enabled and profile is not None and profile.is_published
                ),
            )
