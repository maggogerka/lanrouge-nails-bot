"""Central feature-flag guard for Telegram router boundaries."""

from __future__ import annotations

from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message

from app.domain.errors import EntityNotFoundError, FeatureDisabledError
from app.schemas.features import FeatureName
from app.services.feature_flag_service import FeatureFlagService


class IsFeatureEnabled(BaseFilter):
    """Fail closed when a hidden feature is disabled or has no persisted snapshot."""

    def __init__(self, feature: FeatureName) -> None:
        self._feature = feature

    async def __call__(
        self,
        event: Message | CallbackQuery,
        feature_flag_service: FeatureFlagService,
    ) -> bool:
        del event
        try:
            await feature_flag_service.require_enabled(self._feature)
        except (FeatureDisabledError, EntityNotFoundError):
            return False
        return True


class IsAnyFeatureEnabled(BaseFilter):
    """Fail closed unless a composite UI section has at least one enabled feature."""

    def __init__(self, *features: FeatureName) -> None:
        if not features:
            raise ValueError("at least one feature is required")
        self._features = frozenset(features)

    async def __call__(
        self,
        event: Message | CallbackQuery,
        feature_flag_service: FeatureFlagService,
    ) -> bool:
        del event
        try:
            snapshot = await feature_flag_service.snapshot()
        except EntityNotFoundError:
            return False
        return any(snapshot.enabled(feature) for feature in self._features)
