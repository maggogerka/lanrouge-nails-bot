"""Telegram long-polling process and dependency composition root."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.redis import RedisStorage

from app.config import RuntimeConfigurationError, Settings, get_settings
from app.database import Database
from app.domain.enums import PaymentMode
from app.domain.tenancy import DEFAULT_BUSINESS_ID
from app.handlers import root_router
from app.healthcheck import check_dependencies
from app.logging import configure_logging, log_event
from app.middlewares.correlation import CorrelationIdMiddleware
from app.middlewares.navigation import GlobalNavigationMiddleware
from app.middlewares.staff_context import RuntimeAuthorizationMiddleware
from app.observability import ObservabilityConfigurationError, initialize_observability
from app.payments.http_transport import AioHttpTransport
from app.payments.providers.manual import ManualPaymentProvider
from app.payments.providers.yookassa import YooKassaPaymentProvider
from app.repositories import SqlAlchemyUnitOfWork
from app.runtime_health import (
    BOT_HEARTBEAT_INTERVAL_SECONDS,
    RuntimeHeartbeat,
    open_component_heartbeat,
)
from app.services import (
    AcquisitionAdministrationService,
    AcquisitionRuntimeService,
    AppointmentService,
    AuthorizationService,
    AvailabilityService,
    BookingService,
    BroadcastService,
    BusinessAdministrationService,
    ConsentService,
    CrmService,
    DeletionRequestNotificationService,
    FeatureFlagService,
    FeaturePrerequisites,
    ManualPrepaymentService,
    MarketingEventService,
    MasterProfileService,
    MasterWorkspaceService,
    MenuService,
    PaymentAdministrationService,
    PaymentService,
    PortfolioService,
    PresentationService,
    PrivacyDeletionRuntimeService,
    ReferenceCleanupService,
    RepeatBookingService,
    RescheduleService,
    ReviewService,
    ServiceCatalog,
    SettingsService,
    SubscriptionService,
    VendorSupportService,
    WaitlistService,
)
from app.services.payment_coordinator import ManualPaymentApprovalCoordinator, RefundCoordinator
from app.services.subscription_service import DatabaseSubscriptionStatusProvider

logger = logging.getLogger(__name__)


def create_dispatcher(
    settings: Settings,
    database: Database,
    authorization_service: AuthorizationService | None = None,
    payment_services: Mapping[PaymentMode, PaymentService] | None = None,
) -> Dispatcher:
    """Build a Dispatcher without opening Telegram connections."""

    authorization_service = authorization_service or AuthorizationService(database.sessions)
    # ADMIN_TELEGRAM_IDS is consumed only by startup bootstrap. Runtime legacy
    # services receive no numeric fallback and rely on the DB context middleware.
    runtime_admin_ids: frozenset[int] = frozenset()
    storage = RedisStorage.from_url(
        settings.redis_url.get_secret_value(),
        state_ttl=settings.reference_draft_retention_hours * 60 * 60,
        data_ttl=settings.reference_draft_retention_hours * 60 * 60,
    )
    service_catalog = ServiceCatalog(
        lambda: SqlAlchemyUnitOfWork(database.sessions),
        runtime_admin_ids,
    )
    availability_service = AvailabilityService(
        lambda: SqlAlchemyUnitOfWork(database.sessions),
        runtime_admin_ids,
    )
    consent_service = ConsentService(
        lambda: SqlAlchemyUnitOfWork(database.sessions),
        fallback_privacy_policy_url=(
            str(settings.privacy_policy_url) if settings.privacy_policy_url is not None else None
        ),
    )
    acquisition_service = AcquisitionRuntimeService(
        lambda: SqlAlchemyUnitOfWork(database.sessions),
        fallback_privacy_policy_url=(
            str(settings.privacy_policy_url) if settings.privacy_policy_url is not None else None
        ),
    )
    acquisition_admin_service = AcquisitionAdministrationService(
        lambda: SqlAlchemyUnitOfWork(database.sessions),
        authorization_service,
    )
    privacy_deletion_service = PrivacyDeletionRuntimeService(
        lambda: SqlAlchemyUnitOfWork(database.sessions),
        authorization_service,
    )
    deletion_request_notification_service = DeletionRequestNotificationService(
        authorization_service
    )
    configured_payment_services = dict(
        payment_services or {PaymentMode.MANUAL: PaymentService(ManualPaymentProvider())}
    )
    subscription_service = SubscriptionService(
        DatabaseSubscriptionStatusProvider(lambda: SqlAlchemyUnitOfWork(database.sessions))
    )
    business_service = BusinessAdministrationService(
        lambda: SqlAlchemyUnitOfWork(database.sessions),
        authorization_service,
    )
    booking_service = BookingService(
        lambda: SqlAlchemyUnitOfWork(database.sessions),
        runtime_admin_ids,
        reference_retention_policy=settings.reference_retention_policy,
        payment_services=configured_payment_services,
        payment_return_url=(
            str(settings.yookassa_return_url) if settings.yookassa_return_url is not None else None
        ),
        subscription_service=subscription_service,
    )
    manual_prepayment_service = ManualPrepaymentService(
        lambda: SqlAlchemyUnitOfWork(database.sessions)
    )
    manual_payment_service = configured_payment_services[PaymentMode.MANUAL]
    payment_admin_service = PaymentAdministrationService(
        lambda: SqlAlchemyUnitOfWork(database.sessions),
        authorization_service,
        ManualPaymentApprovalCoordinator(
            lambda: SqlAlchemyUnitOfWork(database.sessions),
            authorization_service,
            manual_payment_service,
        ),
        {
            mode: RefundCoordinator(
                lambda: SqlAlchemyUnitOfWork(database.sessions),
                authorization_service,
                payment_service,
            )
            for mode, payment_service in configured_payment_services.items()
        },
    )
    appointment_service = AppointmentService(
        lambda: SqlAlchemyUnitOfWork(database.sessions),
        runtime_admin_ids,
        reference_retention_policy=settings.reference_retention_policy,
    )
    reschedule_service = RescheduleService(
        lambda: SqlAlchemyUnitOfWork(database.sessions),
        runtime_admin_ids,
        reference_retention_policy=settings.reference_retention_policy,
    )
    settings_service = SettingsService(
        lambda: SqlAlchemyUnitOfWork(database.sessions),
        runtime_admin_ids,
    )
    portfolio_service = PortfolioService(
        lambda: SqlAlchemyUnitOfWork(database.sessions),
        runtime_admin_ids,
    )
    crm_service = CrmService(
        lambda: SqlAlchemyUnitOfWork(database.sessions),
        runtime_admin_ids,
    )
    waitlist_service = WaitlistService(
        lambda: SqlAlchemyUnitOfWork(database.sessions),
        runtime_admin_ids,
    )
    review_service = ReviewService(
        lambda: SqlAlchemyUnitOfWork(database.sessions),
        runtime_admin_ids,
    )
    repeat_booking_service = RepeatBookingService(lambda: SqlAlchemyUnitOfWork(database.sessions))
    broadcast_service = BroadcastService(
        lambda: SqlAlchemyUnitOfWork(database.sessions), runtime_admin_ids
    )
    marketing_event_service = MarketingEventService(lambda: SqlAlchemyUnitOfWork(database.sessions))
    master_profile_service = MasterProfileService(
        lambda: SqlAlchemyUnitOfWork(database.sessions), runtime_admin_ids
    )
    master_workspace_service = MasterWorkspaceService(
        lambda: SqlAlchemyUnitOfWork(database.sessions)
    )
    menu_service = MenuService(lambda: SqlAlchemyUnitOfWork(database.sessions))
    feature_flag_service = FeatureFlagService(
        lambda: SqlAlchemyUnitOfWork(database.sessions),
        FeaturePrerequisites(
            yookassa_ready=bool(
                settings.yookassa_shop_id.get_secret_value()
                and settings.yookassa_secret_key.get_secret_value()
                and settings.yookassa_business_id > 0
                and settings.yookassa_return_url is not None
            ),
            mini_app_ready=bool(
                settings.mini_app_allowed_origins
                and settings.api_session_signing_key.get_secret_value()
                and settings.api_rate_limit_subject_key.get_secret_value()
            ),
        ),
    )
    presentation_service = PresentationService(
        database.sessions,
        fallback_privacy_policy_url=(
            str(settings.privacy_policy_url) if settings.privacy_policy_url is not None else None
        ),
    )
    vendor_support_service = VendorSupportService(settings)
    reference_cleanup_service = ReferenceCleanupService(
        lambda: SqlAlchemyUnitOfWork(database.sessions)
    )
    dispatcher = Dispatcher(
        storage=storage,
        events_isolation=storage.create_isolation(),
        settings=settings,
        authorization_service=authorization_service,
        service_catalog=service_catalog,
        availability_service=availability_service,
        consent_service=consent_service,
        acquisition_service=acquisition_service,
        acquisition_admin_service=acquisition_admin_service,
        privacy_deletion_service=privacy_deletion_service,
        deletion_request_notification_service=deletion_request_notification_service,
        booking_service=booking_service,
        manual_prepayment_service=manual_prepayment_service,
        business_service=business_service,
        subscription_service=subscription_service,
        payment_admin_service=payment_admin_service,
        appointment_service=appointment_service,
        reschedule_service=reschedule_service,
        settings_service=settings_service,
        portfolio_service=portfolio_service,
        crm_service=crm_service,
        waitlist_service=waitlist_service,
        review_service=review_service,
        repeat_booking_service=repeat_booking_service,
        broadcast_service=broadcast_service,
        marketing_event_service=marketing_event_service,
        master_profile_service=master_profile_service,
        master_workspace_service=master_workspace_service,
        menu_service=menu_service,
        feature_flag_service=feature_flag_service,
        presentation_service=presentation_service,
        vendor_support_service=vendor_support_service,
        reference_cleanup_service=reference_cleanup_service,
    )
    dispatcher.update.outer_middleware(CorrelationIdMiddleware())
    dispatcher.update.outer_middleware(RuntimeAuthorizationMiddleware())
    dispatcher.message.outer_middleware(GlobalNavigationMiddleware())
    dispatcher.include_router(root_router)
    return dispatcher


async def run_polling(settings: Settings) -> None:
    """Validate dependencies and run long polling until shutdown."""

    settings.validate_bot_runtime()
    await check_dependencies(settings)
    async with open_component_heartbeat(settings, "bot") as heartbeat:
        await _run_polling(settings, heartbeat)


async def _run_polling(settings: Settings, heartbeat: RuntimeHeartbeat) -> None:
    """Own Telegram and database resources under heartbeat supervision."""

    database = Database.create(settings.database_url.get_secret_value())
    dispatcher: Dispatcher | None = None
    payment_transport: AioHttpTransport | None = None
    if not settings.admin_telegram_ids:
        log_event(logger, logging.WARNING, "configuration.admin_ids_empty")

    try:
        authorization_service = AuthorizationService(database.sessions)
        bootstrap_result = await authorization_service.bootstrap_owners(
            business_id=DEFAULT_BUSINESS_ID,
            telegram_ids=settings.admin_telegram_ids,
        )
        log_event(
            logger,
            logging.INFO,
            "authorization.owner_bootstrap_completed",
            created_count=len(bootstrap_result.created),
            skipped_existing_count=bootstrap_result.skipped_existing_count,
            owner_already_present=bootstrap_result.owner_already_present,
        )
        payment_services: dict[PaymentMode, PaymentService] = {
            PaymentMode.MANUAL: PaymentService(ManualPaymentProvider())
        }
        if (
            settings.yookassa_shop_id.get_secret_value()
            and settings.yookassa_secret_key.get_secret_value()
            and settings.yookassa_return_url is not None
        ):
            payment_transport = AioHttpTransport()
            await payment_transport.start()
            payment_services[PaymentMode.YOOKASSA] = PaymentService(
                YooKassaPaymentProvider(
                    payment_transport,
                    shop_id=settings.yookassa_shop_id.get_secret_value(),
                    secret_key=settings.yookassa_secret_key,
                )
            )
        dispatcher = create_dispatcher(
            settings,
            database,
            authorization_service,
            payment_services,
        )
        log_event(
            logger,
            logging.INFO,
            "bot.starting",
            app_env=settings.app_env.value,
            bootstrap_admin_count=len(settings.admin_telegram_ids),
        )
        async with Bot(
            token=settings.bot_token.get_secret_value(),
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        ) as bot:
            await bot.delete_webhook(drop_pending_updates=False)
            await heartbeat.beat()
            heartbeat_task = asyncio.create_task(
                heartbeat.run_periodically(interval_seconds=BOT_HEARTBEAT_INTERVAL_SECONDS),
                name="bot-heartbeat",
            )
            try:
                await dispatcher.start_polling(
                    bot,
                    allowed_updates=dispatcher.resolve_used_update_types(),
                )
            finally:
                heartbeat_task.cancel()
                await asyncio.gather(heartbeat_task, return_exceptions=True)
    finally:
        if dispatcher is not None:
            await dispatcher.storage.close()
        if payment_transport is not None:
            await payment_transport.close()
        await database.close()
        log_event(logger, logging.INFO, "bot.stopped")


def run() -> None:
    """Load settings and run the bot process."""

    settings = get_settings()
    configure_logging(settings.log_level)
    try:
        initialize_observability(settings)
        asyncio.run(run_polling(settings))
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
