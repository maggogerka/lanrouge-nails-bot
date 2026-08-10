"""Transport-neutral payment provider ports and safe value objects."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal, Protocol

from pydantic import SecretStr

from app.domain.enums import PaymentMode, PaymentStatus, RefundStatus
from app.domain.payments import PaymentType


class PaymentProviderError(RuntimeError):
    """A safe provider error carrying only a bounded machine code."""

    def __init__(self, code: str, *, retryable: bool) -> None:
        self.code = code[:100]
        self.retryable = retryable
        super().__init__(self.code)


class PaymentProviderUnavailableError(PaymentProviderError):
    """A timeout, network error or retryable provider response."""

    def __init__(self, code: str = "payment_provider_unavailable") -> None:
        super().__init__(code, retryable=True)


class PaymentProviderProtocolError(PaymentProviderError):
    """The provider returned an invalid or unsupported safe projection."""

    def __init__(self, code: str = "payment_provider_invalid_response") -> None:
        super().__init__(code, retryable=False)


class PaymentProviderOperationUnsupported(PaymentProviderError):
    """A mode deliberately delegates an operation to a human workflow."""

    def __init__(self, code: str) -> None:
        super().__init__(code, retryable=False)


@dataclass(frozen=True, slots=True)
class PaymentCreateCommand:
    business_id: int
    appointment_id: int
    idempotency_key: str = field(repr=False)
    amount: Decimal
    currency: str
    payment_type: PaymentType
    safe_metadata: Mapping[str, str] = field(default_factory=dict, repr=False)
    return_url: str | None = field(default=None, repr=False)
    description: str = "Оплата услуги"


@dataclass(frozen=True, slots=True)
class PaymentCancelCommand:
    provider_payment_id: str
    idempotency_key: str = field(repr=False)
    amount: Decimal
    currency: str
    safe_metadata: Mapping[str, str] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class PaymentRefundCommand:
    provider_payment_id: str
    idempotency_key: str = field(repr=False)
    amount: Decimal
    currency: str
    reason_code: str
    safe_metadata: Mapping[str, str] = field(default_factory=dict, repr=False)


@dataclass(frozen=True, slots=True)
class ProviderPayment:
    provider: PaymentMode
    provider_payment_id: str
    status: PaymentStatus
    amount: Decimal
    currency: str
    safe_metadata: Mapping[str, str] = field(default_factory=dict, repr=False)
    confirmation_url: str | None = field(default=None, repr=False)
    paid_at: datetime | None = None
    cancelled_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ProviderRefund:
    provider: PaymentMode
    provider_refund_id: str
    provider_payment_id: str
    status: RefundStatus
    amount: Decimal
    currency: str


@dataclass(frozen=True, slots=True)
class ProviderWebhookEvent:
    provider: PaymentMode
    event_key: str
    event_type: str
    provider_object_id: str
    provider_payment_id: str
    payload_sha256: str


class PaymentProvider(Protocol):
    """The only provider surface visible to payment application services."""

    @property
    def mode(self) -> PaymentMode: ...

    @property
    def supports_partial_refunds(self) -> bool: ...

    async def create_payment(self, command: PaymentCreateCommand) -> ProviderPayment: ...

    async def get_payment(self, provider_payment_id: str) -> ProviderPayment: ...

    async def cancel_payment(self, command: PaymentCancelCommand) -> ProviderPayment: ...

    async def refund_payment(self, command: PaymentRefundCommand) -> ProviderRefund: ...

    async def get_refund(self, provider_refund_id: str) -> ProviderRefund: ...

    def parse_webhook(self, payload: Mapping[str, object]) -> ProviderWebhookEvent: ...


@dataclass(frozen=True, slots=True)
class HttpBasicAuth:
    username: str
    password: SecretStr = field(repr=False)


@dataclass(frozen=True, slots=True)
class HttpRequest:
    """HTTP request whose representation cannot expose credentials or JSON payload."""

    method: Literal["GET", "POST"]
    url: str
    headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    basic_auth: HttpBasicAuth | None = field(default=None, repr=False)
    json_body: Mapping[str, object] | None = field(default=None, repr=False)
    timeout_seconds: float = 10.0
    max_response_bytes: int = 65_536


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """Decoded bounded response. Concrete transports must not log its raw body."""

    status_code: int
    json_body: Mapping[str, object] = field(repr=False)


class HttpTransport(Protocol):
    """Injectable async HTTP transport owned by the deployment integration layer."""

    async def request(self, request: HttpRequest) -> HttpResponse: ...
