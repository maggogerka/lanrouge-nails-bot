"""Client-owned payment queries shared by Telegram and HTTP transports."""

from __future__ import annotations

from collections.abc import Callable

from app.domain.errors import EntityNotFoundError
from app.repositories.uow import SqlAlchemyUnitOfWork
from app.schemas.booking import ClientActor
from app.schemas.payment import PaymentView

UnitOfWorkFactory = Callable[[], SqlAlchemyUnitOfWork]


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
