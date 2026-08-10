"""Untrusted YooKassa HTTP notification boundary."""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Protocol

from app.api.contracts import SafeHttpError
from app.payments.providers.base import (
    PaymentProviderError,
    ProviderWebhookEvent,
)
from app.services.payment_coordinator import WebhookDisposition, WebhookProcessingError

__all__ = ["WebhookDisposition", "WebhookProcessingError", "YooKassaWebhookBoundary"]


class YooKassaWebhookParser(Protocol):
    def parse_webhook(self, payload: Mapping[str, object]) -> ProviderWebhookEvent: ...


class AuthoritativeWebhookProcessor(Protocol):
    async def process_untrusted_notification(
        self,
        event: ProviderWebhookEvent,
        *,
        correlation_id: str,
    ) -> WebhookDisposition:
        """Deduplicate, then refetch provider state before changing local state."""
        ...


class _DuplicateJsonKeyError(ValueError):
    pass


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    raise ValueError("unsupported JSON constant")


class YooKassaWebhookBoundary:
    """Parse a bounded notification but delegate truth to authoritative provider GETs."""

    def __init__(
        self,
        parser: YooKassaWebhookParser,
        processor: AuthoritativeWebhookProcessor,
        *,
        max_body_bytes: int = 65_536,
    ) -> None:
        if not 1024 <= max_body_bytes <= 1_048_576:
            raise ValueError("webhook body limit must be between 1024 and 1048576")
        self._parser = parser
        self._processor = processor
        self._max_body_bytes = max_body_bytes

    async def handle(
        self,
        body: bytes,
        *,
        content_type: str,
        correlation_id: str,
    ) -> WebhookDisposition:
        if len(body) > self._max_body_bytes:
            raise SafeHttpError(413, "body_too_large", "Request body is too large.")
        media_type = content_type.partition(";")[0].strip().lower()
        if media_type != "application/json":
            raise SafeHttpError(415, "content_type_unsupported", "Invalid request.")
        try:
            decoded = body.decode("utf-8")
            payload = json.loads(
                decoded,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_json_constant,
            )
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise SafeHttpError(400, "webhook_json_invalid", "Invalid request.") from None
        if not isinstance(payload, dict) or any(not isinstance(key, str) for key in payload):
            raise SafeHttpError(400, "webhook_json_invalid", "Invalid request.")
        try:
            event = self._parser.parse_webhook(payload)
        except PaymentProviderError:
            raise SafeHttpError(400, "webhook_envelope_invalid", "Invalid request.") from None
        try:
            return await self._processor.process_untrusted_notification(
                event,
                correlation_id=correlation_id,
            )
        except WebhookProcessingError as exc:
            if exc.retryable:
                raise SafeHttpError(
                    503,
                    "webhook_processing_unavailable",
                    "Service temporarily unavailable.",
                    headers={"retry-after": "5"},
                ) from None
            raise SafeHttpError(422, "webhook_rejected", "Invalid request.") from None
