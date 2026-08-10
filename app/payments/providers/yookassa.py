"""YooKassa adapter over an injected bounded HTTP transport."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import Final
from urllib.parse import urlparse

from pydantic import SecretStr

from app.domain.enums import PaymentMode, PaymentStatus, RefundStatus
from app.domain.payments import validate_money, validate_safe_metadata
from app.payments.providers.base import (
    HttpBasicAuth,
    HttpRequest,
    HttpResponse,
    HttpTransport,
    PaymentCancelCommand,
    PaymentCreateCommand,
    PaymentProviderError,
    PaymentProviderProtocolError,
    PaymentProviderUnavailableError,
    PaymentRefundCommand,
    ProviderPayment,
    ProviderRefund,
    ProviderWebhookEvent,
)

_API_ROOT: Final[str] = "https://api.yookassa.ru/v3"
_SAFE_PROVIDER_ID: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._-]{1,128}$")
_SAFE_SHOP_ID: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_SAFE_IDEMPOTENCY_KEY: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{15,127}$")
_PAYMENT_STATUSES: Final[dict[str, PaymentStatus]] = {
    "pending": PaymentStatus.PENDING,
    "waiting_for_capture": PaymentStatus.PENDING,
    "succeeded": PaymentStatus.SUCCEEDED,
    "canceled": PaymentStatus.CANCELLED,
}
_REFUND_STATUSES: Final[dict[str, RefundStatus]] = {
    "pending": RefundStatus.PENDING,
    "succeeded": RefundStatus.SUCCEEDED,
    "canceled": RefundStatus.CANCELLED,
    "failed": RefundStatus.FAILED,
}
_WEBHOOK_EVENTS: Final[frozenset[str]] = frozenset(
    {
        "payment.waiting_for_capture",
        "payment.succeeded",
        "payment.canceled",
        "refund.succeeded",
    }
)
_MAX_WEBHOOK_BYTES: Final[int] = 65_536


class YooKassaPaymentProvider:
    """Create and verify YooKassa operations without retaining raw provider payloads."""

    mode = PaymentMode.YOOKASSA
    supports_partial_refunds = True

    def __init__(self, transport: HttpTransport, *, shop_id: str, secret_key: SecretStr) -> None:
        normalized_shop_id = shop_id.strip()
        if not _SAFE_SHOP_ID.fullmatch(normalized_shop_id):
            raise ValueError("YooKassa shop_id contains unsupported characters")
        if not secret_key.get_secret_value():
            raise ValueError("YooKassa secret key is required")
        self._transport = transport
        self._auth = HttpBasicAuth(username=normalized_shop_id, password=secret_key)

    async def create_payment(self, command: PaymentCreateCommand) -> ProviderPayment:
        if command.return_url is None or not self._is_safe_https_url(command.return_url):
            raise PaymentProviderProtocolError("yookassa_https_return_url_required")
        metadata = validate_safe_metadata(command.safe_metadata)
        body: dict[str, object] = {
            "amount": self._money_body(command.amount, command.currency),
            "capture": True,
            "confirmation": {"type": "redirect", "return_url": command.return_url},
            "description": command.description[:128],
            "metadata": metadata,
        }
        response = await self._send(
            HttpRequest(
                method="POST",
                url=f"{_API_ROOT}/payments",
                headers=self._idempotency_headers(command.idempotency_key),
                basic_auth=self._auth,
                json_body=body,
            )
        )
        return self._payment_from_response(response.json_body)

    async def get_payment(self, provider_payment_id: str) -> ProviderPayment:
        safe_id = self._provider_id(provider_payment_id)
        response = await self._send(
            HttpRequest(
                method="GET",
                url=f"{_API_ROOT}/payments/{safe_id}",
                basic_auth=self._auth,
            )
        )
        return self._payment_from_response(response.json_body)

    async def cancel_payment(self, command: PaymentCancelCommand) -> ProviderPayment:
        safe_id = self._provider_id(command.provider_payment_id)
        response = await self._send(
            HttpRequest(
                method="POST",
                url=f"{_API_ROOT}/payments/{safe_id}/cancel",
                headers=self._idempotency_headers(command.idempotency_key),
                basic_auth=self._auth,
                json_body={},
            )
        )
        return self._payment_from_response(response.json_body)

    async def refund_payment(self, command: PaymentRefundCommand) -> ProviderRefund:
        safe_id = self._provider_id(command.provider_payment_id)
        response = await self._send(
            HttpRequest(
                method="POST",
                url=f"{_API_ROOT}/refunds",
                headers=self._idempotency_headers(command.idempotency_key),
                basic_auth=self._auth,
                json_body={
                    "payment_id": safe_id,
                    "amount": self._money_body(command.amount, command.currency),
                    "description": command.reason_code,
                },
            )
        )
        return self._refund_from_response(response.json_body)

    async def get_refund(self, provider_refund_id: str) -> ProviderRefund:
        safe_id = self._provider_id(provider_refund_id)
        response = await self._send(
            HttpRequest(
                method="GET",
                url=f"{_API_ROOT}/refunds/{safe_id}",
                basic_auth=self._auth,
            )
        )
        return self._refund_from_response(response.json_body)

    def parse_webhook(self, payload: Mapping[str, object]) -> ProviderWebhookEvent:
        """Extract a dedupe envelope; callers must still perform authoritative GET."""

        try:
            canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        except (TypeError, ValueError) as exc:
            raise PaymentProviderProtocolError("yookassa_webhook_not_json") from exc
        if len(canonical) > _MAX_WEBHOOK_BYTES:
            raise PaymentProviderProtocolError("yookassa_webhook_too_large")
        event_type = self._required_string(payload, "event", max_length=64)
        if event_type not in _WEBHOOK_EVENTS:
            raise PaymentProviderProtocolError("yookassa_webhook_event_unsupported")
        provider_object = self._required_mapping(payload, "object")
        object_id = self._provider_id(self._required_string(provider_object, "id", max_length=128))
        if event_type.startswith("payment."):
            provider_payment_id = object_id
        else:
            provider_payment_id = self._provider_id(
                self._required_string(provider_object, "payment_id", max_length=128)
            )
        digest = sha256(canonical).hexdigest()
        return ProviderWebhookEvent(
            provider=self.mode,
            event_key=digest,
            event_type=event_type,
            provider_object_id=object_id,
            provider_payment_id=provider_payment_id,
            payload_sha256=digest,
        )

    async def _send(self, request: HttpRequest) -> HttpResponse:
        try:
            response = await self._transport.request(request)
        except PaymentProviderError:
            raise
        except Exception:
            # Do not chain arbitrary transport errors: they may embed headers or bodies.
            raise PaymentProviderUnavailableError() from None
        if 200 <= response.status_code < 300:
            return response
        if response.status_code == 429 or response.status_code >= 500:
            raise PaymentProviderUnavailableError("yookassa_temporary_http_error")
        raise PaymentProviderProtocolError("yookassa_rejected_request")

    def _payment_from_response(self, payload: Mapping[str, object]) -> ProviderPayment:
        provider_payment_id = self._provider_id(
            self._required_string(payload, "id", max_length=128)
        )
        raw_status = self._required_string(payload, "status", max_length=32)
        try:
            status = _PAYMENT_STATUSES[raw_status]
        except KeyError as exc:
            raise PaymentProviderProtocolError("yookassa_payment_status_unknown") from exc
        amount, currency = self._parse_money(self._required_mapping(payload, "amount"))
        metadata = self._safe_metadata(payload.get("metadata"))
        confirmation_url: str | None = None
        confirmation = payload.get("confirmation")
        if isinstance(confirmation, Mapping):
            raw_url = confirmation.get("confirmation_url")
            if raw_url is not None:
                if not isinstance(raw_url, str) or len(raw_url) > 2048:
                    raise PaymentProviderProtocolError("yookassa_confirmation_url_invalid")
                if not self._is_safe_https_url(raw_url):
                    raise PaymentProviderProtocolError("yookassa_confirmation_url_not_https")
                confirmation_url = raw_url
        return ProviderPayment(
            provider=self.mode,
            provider_payment_id=provider_payment_id,
            status=status,
            amount=amount,
            currency=currency,
            safe_metadata=metadata,
            confirmation_url=confirmation_url,
            paid_at=self._optional_datetime(payload.get("captured_at")),
            cancelled_at=self._optional_datetime(payload.get("canceled_at")),
        )

    def _refund_from_response(self, payload: Mapping[str, object]) -> ProviderRefund:
        provider_refund_id = self._provider_id(self._required_string(payload, "id", max_length=128))
        provider_payment_id = self._provider_id(
            self._required_string(payload, "payment_id", max_length=128)
        )
        raw_status = self._required_string(payload, "status", max_length=32)
        try:
            status = _REFUND_STATUSES[raw_status]
        except KeyError as exc:
            raise PaymentProviderProtocolError("yookassa_refund_status_unknown") from exc
        amount, currency = self._parse_money(self._required_mapping(payload, "amount"))
        return ProviderRefund(
            provider=self.mode,
            provider_refund_id=provider_refund_id,
            provider_payment_id=provider_payment_id,
            status=status,
            amount=amount,
            currency=currency,
        )

    @staticmethod
    def _idempotency_headers(idempotency_key: str) -> dict[str, str]:
        if not _SAFE_IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise PaymentProviderProtocolError("provider_idempotency_key_invalid")
        return {"Idempotence-Key": idempotency_key, "Content-Type": "application/json"}

    @staticmethod
    def _is_safe_https_url(value: str) -> bool:
        if len(value) > 2048:
            return False
        parsed = urlparse(value)
        return (
            parsed.scheme == "https"
            and parsed.hostname is not None
            and parsed.username is None
            and parsed.password is None
        )

    @staticmethod
    def _provider_id(value: str) -> str:
        if not _SAFE_PROVIDER_ID.fullmatch(value):
            raise PaymentProviderProtocolError("provider_object_id_invalid")
        return value

    @staticmethod
    def _money_body(amount: Decimal, currency: str) -> dict[str, str]:
        normalized_amount, normalized_currency = validate_money(amount, currency)
        return {"value": f"{normalized_amount:.2f}", "currency": normalized_currency}

    def _parse_money(self, payload: Mapping[str, object]) -> tuple[Decimal, str]:
        value = self._required_string(payload, "value", max_length=32)
        currency = self._required_string(payload, "currency", max_length=3)
        try:
            amount = Decimal(value)
            return validate_money(amount, currency)
        except (InvalidOperation, ValueError) as exc:
            raise PaymentProviderProtocolError("yookassa_amount_invalid") from exc

    @staticmethod
    def _required_mapping(payload: Mapping[str, object], key: str) -> Mapping[str, object]:
        value = payload.get(key)
        if not isinstance(value, Mapping) or any(not isinstance(k, str) for k in value):
            raise PaymentProviderProtocolError(f"yookassa_{key}_invalid")
        return value

    @staticmethod
    def _required_string(payload: Mapping[str, object], key: str, *, max_length: int) -> str:
        value = payload.get(key)
        if not isinstance(value, str) or not value or len(value) > max_length:
            raise PaymentProviderProtocolError(f"yookassa_{key}_invalid")
        return value

    @staticmethod
    def _safe_metadata(value: object) -> dict[str, str]:
        if value is None:
            return {}
        if not isinstance(value, Mapping):
            raise PaymentProviderProtocolError("yookassa_metadata_invalid")
        if any(
            not isinstance(key, str) or not isinstance(item, str) for key, item in value.items()
        ):
            raise PaymentProviderProtocolError("yookassa_metadata_invalid")
        try:
            return validate_safe_metadata({str(key): str(item) for key, item in value.items()})
        except ValueError as exc:
            raise PaymentProviderProtocolError("yookassa_metadata_unsafe") from exc

    @staticmethod
    def _optional_datetime(value: object) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, str) or len(value) > 64:
            raise PaymentProviderProtocolError("yookassa_timestamp_invalid")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise PaymentProviderProtocolError("yookassa_timestamp_invalid") from exc
        if parsed.tzinfo is None:
            raise PaymentProviderProtocolError("yookassa_timestamp_naive")
        return parsed
