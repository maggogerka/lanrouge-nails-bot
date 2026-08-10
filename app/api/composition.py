"""Typed composition port for the v1 API process.

This module intentionally does not read environment variables or create a
placeholder session issuer.  The executable composition root must supply every
security-sensitive dependency explicitly.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from app.api.application import (
    ApiApplication,
    MiniAppProductRouter,
    MiniAppSessionIssuer,
    MiniAppSessionResolver,
)
from app.api.contracts import LifecycleResource, ReadinessProbe
from app.api.rate_limit import SharedHttpRateLimiter
from app.api.telegram_auth import TelegramInitDataVerifier
from app.api.webhooks import YooKassaWebhookBoundary


@dataclass(frozen=True, slots=True)
class ApiRuntimeOptions:
    allowed_hosts: Collection[str]
    allowed_origins: Collection[str]
    enforce_https: bool = True
    max_body_bytes: int = 65_536
    readiness_timeout_seconds: float = 3.0


@dataclass(frozen=True, slots=True)
class ApiDependencies:
    readiness_probe: ReadinessProbe
    telegram_verifier: TelegramInitDataVerifier
    session_issuer: MiniAppSessionIssuer
    rate_limiter: SharedHttpRateLimiter
    session_resolver: MiniAppSessionResolver | None = None
    product_api: MiniAppProductRouter | None = None
    yookassa_webhook: YooKassaWebhookBoundary | None = None
    lifecycle_resources: tuple[LifecycleResource, ...] = ()


def create_api_application(
    options: ApiRuntimeOptions,
    dependencies: ApiDependencies,
) -> ApiApplication:
    """Build the ASGI app after the outer composition root validates settings."""

    return ApiApplication(
        allowed_hosts=options.allowed_hosts,
        allowed_origins=options.allowed_origins,
        readiness_probe=dependencies.readiness_probe,
        telegram_verifier=dependencies.telegram_verifier,
        session_issuer=dependencies.session_issuer,
        rate_limiter=dependencies.rate_limiter,
        session_resolver=dependencies.session_resolver,
        product_api=dependencies.product_api,
        yookassa_webhook=dependencies.yookassa_webhook,
        lifecycle_resources=dependencies.lifecycle_resources,
        enforce_https=options.enforce_https,
        max_body_bytes=options.max_body_bytes,
        readiness_timeout_seconds=options.readiness_timeout_seconds,
    )
