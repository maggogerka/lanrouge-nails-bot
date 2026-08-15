"""In-transaction feature checks for application services and workers."""

from __future__ import annotations

from app.domain.errors import EntityNotFoundError, FeatureDisabledError
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.features import FeatureName


async def require_feature(
    unit_of_work: SqlAlchemyUnitOfWork,
    feature: FeatureName,
) -> None:
    flags = await unit_of_work.features.get()
    if flags is None:
        raise EntityNotFoundError("Business feature settings are missing")
    if not bool(getattr(flags, feature.value, False)):
        raise FeatureDisabledError("This bot feature is disabled by the business owner")


async def is_feature_enabled(
    unit_of_work: SqlAlchemyUnitOfWork,
    feature: FeatureName,
) -> bool:
    """Return a fail-closed feature decision inside the caller's transaction."""

    flags = await unit_of_work.features.get()
    return flags is not None and bool(getattr(flags, feature.value, False))
