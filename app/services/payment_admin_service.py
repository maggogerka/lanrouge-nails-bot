"""Live-authorized administration facade for payment operations."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from app.domain.enums import PaymentMode, PaymentStatus, RefundStatus
from app.domain.errors import EntityNotFoundError
from app.domain.payments import PaymentStateError
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.authorization import StaffContext, StaffPermission
from app.schemas.payment import PaymentSettingsView, PaymentView, RefundCreate, RefundView
from app.services.authorization_service import AuthorizationService
from app.services.payment_coordinator import (
    ManualPaymentApprovalCoordinator,
    RefundCoordinator,
    RefundOutcome,
)

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


class PaymentAdministrationService:
    """Keep Telegram handlers free from financial state transitions."""

    def __init__(
        self,
        unit_of_work_factory: UnitOfWorkFactory,
        authorization_service: AuthorizationService,
        manual_approval: ManualPaymentApprovalCoordinator,
        refund_coordinators: Mapping[PaymentMode, RefundCoordinator] | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._authorization = authorization_service
        self._manual_approval = manual_approval
        self._refund_coordinators = dict(refund_coordinators or {})
        self._available_modes = frozenset(self._refund_coordinators)

    async def get_settings(self, actor: StaffContext) -> PaymentSettingsView:
        live_actor = await self._authorization.authorize(
            business_id=actor.business_id,
            telegram_id=actor.telegram_id,
            permission=StaffPermission.VIEW_PAYMENTS,
        )
        async with self._unit_of_work_factory() as unit_of_work:
            if unit_of_work.business_id != live_actor.business_id:
                raise RuntimeError("payment unit of work tenant mismatch")
            settings = await unit_of_work.reservations.payment_settings()
            if settings is None:
                raise EntityNotFoundError("Настройки оплаты не найдены.")
            return PaymentSettingsView.model_validate(settings)

    async def set_mode(
        self,
        actor: StaffContext,
        mode: PaymentMode,
        *,
        confirmed: bool,
        correlation_id: str | None = None,
    ) -> PaymentSettingsView:
        if not confirmed:
            raise PaymentStateError("Требуется явное подтверждение изменения режима.")
        live_actor = await self._authorization.authorize(
            business_id=actor.business_id,
            telegram_id=actor.telegram_id,
            permission=StaffPermission.MANAGE_PRIVATE_SETTINGS,
        )
        if mode is not PaymentMode.DISABLED and mode not in self._available_modes:
            raise PaymentStateError("Платёжный провайдер не настроен на сервере.")
        async with self._unit_of_work_factory() as unit_of_work:
            if unit_of_work.business_id != live_actor.business_id:
                raise RuntimeError("payment unit of work tenant mismatch")
            settings = await unit_of_work.reservations.payment_settings(for_update=True)
            flags = await unit_of_work.features.get()
            if settings is None or flags is None:
                raise EntityNotFoundError("Настройки оплаты не найдены.")
            if mode is PaymentMode.MANUAL:
                if not (settings.manual_payment_instructions or "").strip():
                    raise PaymentStateError("Сначала заполните инструкции ручной оплаты.")
                if not flags.prepayment or not flags.manual_payments:
                    raise PaymentStateError(
                        "Сначала включите «Предоплата» и «Ручная оплата» в функциях бота."
                    )
            if mode is PaymentMode.YOOKASSA and (
                not flags.prepayment or not flags.yookassa_payments
            ):
                raise PaymentStateError(
                    "Сначала включите «Предоплата» и «YooKassa» в функциях бота."
                )
            previous = settings.mode
            settings.mode = mode
            settings.version += 1
            await unit_of_work.audit.add(
                actor_user_id=live_actor.user_id,
                action="payment.settings_mode_changed",
                entity_type="business_payment_settings",
                entity_id=str(live_actor.business_id),
                changes={"previous": previous.value, "new": mode.value},
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return PaymentSettingsView.model_validate(settings)

    async def set_manual_instructions(
        self,
        actor: StaffContext,
        instructions: str,
        *,
        correlation_id: str | None = None,
    ) -> PaymentSettingsView:
        live_actor = await self._authorization.authorize(
            business_id=actor.business_id,
            telegram_id=actor.telegram_id,
            permission=StaffPermission.MANAGE_PRIVATE_SETTINGS,
        )
        normalized = instructions.strip()
        if not 1 <= len(normalized) <= 2000:
            raise PaymentStateError("Инструкция должна содержать от 1 до 2000 символов.")
        forbidden = ("cvv", "cvc", "sms-код", "смс-код", "срок действия карты")
        if any(marker in normalized.casefold() for marker in forbidden):
            raise PaymentStateError("Нельзя запрашивать CVV/CVC, SMS-коды или срок действия карты.")
        async with self._unit_of_work_factory() as unit_of_work:
            if unit_of_work.business_id != live_actor.business_id:
                raise RuntimeError("payment unit of work tenant mismatch")
            settings = await unit_of_work.reservations.payment_settings(for_update=True)
            if settings is None:
                raise EntityNotFoundError("Настройки оплаты не найдены.")
            settings.manual_payment_instructions = normalized
            settings.version += 1
            await unit_of_work.audit.add(
                actor_user_id=live_actor.user_id,
                action="payment.manual_instructions_changed",
                entity_type="business_payment_settings",
                entity_id=str(live_actor.business_id),
                changes={"configured": True},
                correlation_id=correlation_id,
            )
            await unit_of_work.commit()
            return PaymentSettingsView.model_validate(settings)

    async def list_recent(
        self,
        actor: StaffContext,
        *,
        limit: int = 30,
    ) -> tuple[PaymentView, ...]:
        live_actor = await self._authorization.authorize(
            business_id=actor.business_id,
            telegram_id=actor.telegram_id,
            permission=StaffPermission.VIEW_PAYMENTS,
        )
        async with self._unit_of_work_factory() as unit_of_work:
            if unit_of_work.business_id != live_actor.business_id:
                raise RuntimeError("payment unit of work tenant mismatch")
            rows = await unit_of_work.payments.list_recent(
                statuses={
                    PaymentStatus.CREATED,
                    PaymentStatus.PENDING,
                    PaymentStatus.SUCCEEDED,
                    PaymentStatus.REFUND_PENDING,
                    PaymentStatus.PARTIALLY_REFUNDED,
                    PaymentStatus.REFUNDED,
                },
                limit=limit,
            )
            return tuple(PaymentView.model_validate(row) for row in rows)

    async def approve_manual(
        self,
        actor: StaffContext,
        payment_id: int,
        *,
        correlation_id: str | None = None,
    ) -> PaymentView:
        return await self._manual_approval.approve(
            actor,
            payment_id,
            correlation_id=correlation_id,
        )

    async def request_remaining_refund(
        self,
        actor: StaffContext,
        payment_id: int,
        *,
        correlation_id: str | None = None,
    ) -> RefundOutcome:
        live_actor = await self._authorization.authorize(
            business_id=actor.business_id,
            telegram_id=actor.telegram_id,
            permission=StaffPermission.REFUND_PAYMENTS,
        )
        async with self._unit_of_work_factory() as unit_of_work:
            if unit_of_work.business_id != live_actor.business_id:
                raise RuntimeError("payment unit of work tenant mismatch")
            payment = await unit_of_work.payments.get(payment_id)
            if payment is None:
                raise EntityNotFoundError("Платёж не найден.")
            remaining = payment.amount - payment.refunded_amount
            if (
                payment.status
                not in {
                    PaymentStatus.SUCCEEDED,
                    PaymentStatus.PARTIALLY_REFUNDED,
                }
                or remaining <= 0
            ):
                raise PaymentStateError("Для этого платежа возврат недоступен.")
            coordinator = self._refund_coordinators.get(payment.provider)
            if coordinator is None:
                raise PaymentStateError("Провайдер возврата не настроен.")
            values = RefundCreate(
                business_id=payment.business_id,
                payment_id=payment.id,
                amount=remaining,
                currency=payment.currency,
                idempotency_key=(
                    f"tg-refund:{payment.id}:{payment.refunded_amount:.2f}:{remaining:.2f}"
                ),
                reason_code="requested_by_business",
                requested_by_user_id=live_actor.user_id,
                correlation_id=correlation_id,
                safe_metadata={"appointment_id": str(payment.appointment_id)},
            )
        return await coordinator.create_and_submit(live_actor, values)

    async def approve_manual_refund(
        self,
        actor: StaffContext,
        refund_id: int,
        *,
        correlation_id: str | None = None,
    ) -> RefundView:
        coordinator = self._refund_coordinators.get(PaymentMode.MANUAL)
        if coordinator is None:
            raise PaymentStateError("Ручные возвраты не настроены.")
        refund = await coordinator.approve_manual_refund(
            actor,
            refund_id,
            correlation_id=correlation_id,
        )
        if refund.status is not RefundStatus.SUCCEEDED:
            raise PaymentStateError("Возврат не подтверждён.")
        return refund
