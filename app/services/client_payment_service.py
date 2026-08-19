"""Client-owned payment queries shared by Telegram and HTTP transports."""

from __future__ import annotations

from collections.abc import Callable

from app.database.models.appointment import Appointment
from app.database.models.payment import Payment
from app.domain.enums import PaymentMode, PaymentStatus
from app.domain.errors import EntityNotFoundError
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.booking import ClientActor
from app.schemas.pagination import Page, PageRequest
from app.schemas.payment import ClientPaymentSection, ClientPaymentView, PaymentView

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]
_ACTIVE_STATUSES = frozenset(
    {PaymentStatus.CREATED, PaymentStatus.PENDING, PaymentStatus.REFUND_PENDING}
)
_HISTORY_STATUSES = frozenset(set(PaymentStatus) - _ACTIVE_STATUSES)


class ClientPaymentService:
    """Project one payment only after checking appointment ownership."""

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    async def get_my(self, actor: ClientActor, payment_id: int) -> PaymentView:
        if payment_id <= 0:
            raise EntityNotFoundError("Payment was not found")
        async with self._unit_of_work_factory() as unit_of_work:
            client = await unit_of_work.users.get_by_telegram_id(actor.telegram_id)
            payment = await unit_of_work.payments.get(payment_id)
            if client is None or payment is None:
                raise EntityNotFoundError("Payment was not found")
            appointment = await unit_of_work.appointments.get(payment.appointment_id)
            if appointment is None or appointment.client_id != client.id:
                raise EntityNotFoundError("Payment was not found")
            return PaymentView.model_validate(payment)

    async def list_my_page(
        self,
        actor: ClientActor,
        section: ClientPaymentSection,
        page: PageRequest,
    ) -> Page[ClientPaymentView]:
        statuses = self._statuses(section)
        async with self._unit_of_work_factory() as unit_of_work:
            client = await unit_of_work.users.get_by_telegram_id(actor.telegram_id)
            if client is None:
                return Page(items=[], total=0, page=page.page, page_size=page.page_size)
            settings = await unit_of_work.settings.get()
            payment_settings = await unit_of_work.reservations.payment_settings()
            if settings is None:
                return Page(items=[], total=0, page=page.page, page_size=page.page_size)
            total = await unit_of_work.payments.count_for_client(client.id, statuses=statuses)
            rows = await unit_of_work.payments.list_for_client(
                client.id,
                statuses=statuses,
                limit=page.page_size,
                offset=page.offset,
            )
            instructions = (
                payment_settings.manual_payment_instructions
                if payment_settings is not None
                else None
            )
            return Page(
                items=[
                    self._client_view(payment, appointment, settings.timezone, instructions)
                    for payment, appointment in rows
                ],
                total=total,
                page=page.page,
                page_size=page.page_size,
            )

    async def get_my_counts(self, actor: ClientActor) -> tuple[int, int]:
        """Count both sections in one short client-scoped unit of work."""

        async with self._unit_of_work_factory() as unit_of_work:
            client = await unit_of_work.users.get_by_telegram_id(actor.telegram_id)
            if client is None:
                return 0, 0
            active = await unit_of_work.payments.count_for_client(
                client.id, statuses=_ACTIVE_STATUSES
            )
            history = await unit_of_work.payments.count_for_client(
                client.id, statuses=_HISTORY_STATUSES
            )
            return active, history

    async def get_my_details(
        self,
        actor: ClientActor,
        payment_id: int,
    ) -> ClientPaymentView:
        if payment_id <= 0:
            raise EntityNotFoundError("Оплата не найдена.")
        async with self._unit_of_work_factory() as unit_of_work:
            client = await unit_of_work.users.get_by_telegram_id(actor.telegram_id)
            if client is None:
                raise EntityNotFoundError("Оплата не найдена.")
            row = await unit_of_work.payments.get_for_client(payment_id, client.id)
            settings = await unit_of_work.settings.get()
            payment_settings = await unit_of_work.reservations.payment_settings()
            if row is None or settings is None:
                raise EntityNotFoundError("Оплата не найдена.")
            instructions = (
                payment_settings.manual_payment_instructions
                if payment_settings is not None
                else None
            )
            return self._client_view(*row, settings.timezone, instructions)

    @staticmethod
    def _statuses(section: ClientPaymentSection) -> frozenset[PaymentStatus]:
        return _ACTIVE_STATUSES if section is ClientPaymentSection.ACTIVE else _HISTORY_STATUSES

    @staticmethod
    def _client_view(
        payment: Payment,
        appointment: Appointment,
        timezone: str,
        manual_payment_instructions: str | None,
    ) -> ClientPaymentView:
        base = PaymentView.model_validate(payment)
        show_instructions = (
            payment.provider is PaymentMode.MANUAL
            and payment.status in _ACTIVE_STATUSES
            and bool(manual_payment_instructions)
        )
        return ClientPaymentView(
            **base.model_dump(),
            created_at=payment.created_at,
            appointment_start_at=appointment.scheduled_start_at,
            appointment_end_at=appointment.scheduled_end_at,
            timezone=timezone,
            service_name=appointment.service_name_snapshot,
            master_name=appointment.master_name_snapshot,
            manual_payment_instructions=(
                manual_payment_instructions if show_instructions else None
            ),
        )
