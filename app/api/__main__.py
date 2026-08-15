"""Executable production composition root for the HTTP API."""

from __future__ import annotations

import logging
from typing import cast

import uvicorn
from redis.asyncio import Redis

from app.api.application import ApiApplication
from app.api.composition import ApiDependencies, ApiRuntimeOptions, create_api_application
from app.api.contracts import LifecycleResource
from app.api.product import MiniAppProductApi
from app.api.rate_limit import SharedHttpRateLimiter
from app.api.readiness import ApiReadinessProbe, RedisPingClient
from app.api.runtime import CloseOnlyResource
from app.api.sessions import (
    OpaqueSessionIssuer,
    RedisOpaqueSessionStore,
    RedisSessionClient,
)
from app.api.telegram_auth import RedisReplayStore, RedisSetClient, TelegramInitDataVerifier
from app.api.webhooks import YooKassaWebhookBoundary
from app.config import RuntimeConfigurationError, Settings, get_settings
from app.database import Database
from app.domain.enums import PaymentMode
from app.logging import configure_logging, log_event
from app.observability import ObservabilityConfigurationError, initialize_observability
from app.payments.http_transport import AioHttpTransport
from app.payments.providers.manual import ManualPaymentProvider
from app.payments.providers.yookassa import YooKassaPaymentProvider
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.security.rate_limit import RedisEvalClient, RedisRateLimiter
from app.services.appointment_service import AppointmentService
from app.services.booking_service import BookingService
from app.services.client_payment_service import ClientPaymentService
from app.services.consent_service import ConsentService
from app.services.payment_coordinator import (
    PaymentUnitOfWorkFactory,
    YooKassaWebhookLifecycleCoordinator,
)
from app.services.payment_service import PaymentService
from app.services.presentation_service import PresentationService
from app.services.reschedule_service import RescheduleService
from app.services.subscription_service import (
    DatabaseSubscriptionStatusProvider,
    SubscriptionService,
)

logger = logging.getLogger(__name__)


def create_application(settings: Settings | None = None) -> ApiApplication:
    """Validate all API/provider secrets and build an app without opening sockets."""

    runtime = settings or get_settings()
    runtime.validate_api_runtime()

    yookassa_values_present = runtime.yookassa_values_present
    if yookassa_values_present:
        runtime.validate_yookassa_runtime()

    database = Database.from_settings(runtime)
    redis = Redis.from_url(
        runtime.redis_url.get_secret_value(),
        socket_connect_timeout=3,
        socket_timeout=5,
        health_check_interval=30,
        decode_responses=False,
    )
    business_id = runtime.yookassa_business_id

    def unit_of_work_factory() -> SqlAlchemyUnitOfWork:
        return SqlAlchemyUnitOfWork(
            database.sessions,
            business_id=business_id,
        )

    uow_factory = cast(
        PaymentUnitOfWorkFactory,
        unit_of_work_factory,
    )
    payment_services = {
        PaymentMode.MANUAL: PaymentService(ManualPaymentProvider()),
    }
    transport: AioHttpTransport | None = None
    webhook: YooKassaWebhookBoundary | None = None
    if yookassa_values_present:
        transport = AioHttpTransport()
        provider = YooKassaPaymentProvider(
            transport,
            shop_id=runtime.yookassa_shop_id.get_secret_value(),
            secret_key=runtime.yookassa_secret_key,
        )
        payment_service = PaymentService(provider)
        payment_services[PaymentMode.YOOKASSA] = payment_service
        webhook_processor = YooKassaWebhookLifecycleCoordinator(
            uow_factory,
            payment_service,
            business_id=business_id,
            retention_days=runtime.yookassa_webhook_retention_days,
        )
        webhook = YooKassaWebhookBoundary(provider, webhook_processor)

    replay_store = RedisReplayStore(
        cast(RedisSetClient, redis),
        namespace=runtime.redis_namespace,
    )
    verifier = TelegramInitDataVerifier(
        runtime.bot_token,
        replay_store,
        max_age_seconds=runtime.telegram_init_data_ttl_seconds,
    )
    session_store = RedisOpaqueSessionStore(
        cast(RedisSessionClient, redis),
        runtime.api_session_signing_key,
        namespace=runtime.redis_namespace,
    )
    session_issuer = OpaqueSessionIssuer(
        session_store,
        ttl_seconds=runtime.api_session_ttl_seconds,
    )
    limiter = SharedHttpRateLimiter(
        RedisRateLimiter(
            cast(RedisEvalClient, redis),
            namespace=runtime.redis_namespace,
        ),
        runtime.api_rate_limit_subject_key,
        business_id=business_id,
    )
    readiness = ApiReadinessProbe(database.engine, cast(RedisPingClient, redis))
    product_api = MiniAppProductApi(
        presentation=PresentationService(
            database.sessions,
            business_id=business_id,
            fallback_privacy_policy_url=(
                str(runtime.privacy_policy_url) if runtime.privacy_policy_url is not None else None
            ),
        ),
        booking=BookingService(
            unit_of_work_factory,
            frozenset(),
            reference_retention_policy=runtime.reference_retention_policy,
            payment_services=payment_services,
            payment_return_url=(
                str(runtime.yookassa_return_url)
                if runtime.yookassa_return_url is not None
                else None
            ),
            subscription_service=SubscriptionService(
                DatabaseSubscriptionStatusProvider(unit_of_work_factory)
            ),
            business_id=business_id,
        ),
        appointments=AppointmentService(
            unit_of_work_factory,
            frozenset(),
            reference_retention_policy=runtime.reference_retention_policy,
        ),
        reschedule=RescheduleService(
            unit_of_work_factory,
            frozenset(),
            reference_retention_policy=runtime.reference_retention_policy,
        ),
        consents=ConsentService(
            unit_of_work_factory,
            fallback_privacy_policy_url=(
                str(runtime.privacy_policy_url) if runtime.privacy_policy_url is not None else None
            ),
        ),
        payments=ClientPaymentService(unit_of_work_factory),
    )

    lifecycle_resources: list[LifecycleResource] = [
        CloseOnlyResource(database.close),
        CloseOnlyResource(redis.aclose),
    ]
    if transport is not None:
        lifecycle_resources.append(transport)

    return create_api_application(
        ApiRuntimeOptions(
            allowed_hosts=runtime.api_allowed_hosts,
            allowed_origins=runtime.mini_app_allowed_origins,
            enforce_https=runtime.api_enforce_https,
            max_body_bytes=runtime.api_max_body_bytes,
            readiness_timeout_seconds=runtime.api_readiness_timeout_seconds,
        ),
        ApiDependencies(
            readiness_probe=readiness,
            telegram_verifier=verifier,
            session_issuer=session_issuer,
            rate_limiter=limiter,
            session_resolver=session_store,
            product_api=product_api,
            yookassa_webhook=webhook,
            lifecycle_resources=tuple(lifecycle_resources),
        ),
    )


def run() -> None:
    """Run one ASGI process with fail-closed proxy trust and no access-body logging."""

    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        settings.validate_api_runtime()
        initialize_observability(settings)
        application = create_application(settings)
        uvicorn.run(
            application,
            host=settings.api_host,
            port=settings.api_port,
            proxy_headers=True,
            forwarded_allow_ips=list(settings.api_trusted_proxy_ips),
            server_header=False,
            access_log=False,
            log_config=None,
            lifespan="on",
            limit_concurrency=200,
            backlog=256,
            timeout_keep_alive=5,
            timeout_graceful_shutdown=30,
            h11_max_incomplete_event_size=16_384,
        )
    except RuntimeConfigurationError as exc:
        log_event(logger, logging.CRITICAL, "configuration.invalid", missing=exc.missing)
        raise SystemExit(2) from exc
    except ObservabilityConfigurationError as exc:
        log_event(
            logger,
            logging.CRITICAL,
            "observability.configuration_invalid",
            error_code="sentry_initialization_failed",
        )
        raise SystemExit(2) from exc


if __name__ == "__main__":
    run()
