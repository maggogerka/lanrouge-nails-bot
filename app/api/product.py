"""Versioned Mini App routes delegating all business rules to application services."""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any
from urllib.parse import parse_qsl
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.api.contracts import HttpRequest, HttpResponse, SafeHttpError
from app.api.sessions import OpaqueSession
from app.domain.enums import ConsentSource
from app.domain.legal import (
    MARKETING_CONSENT_SHA256,
    MARKETING_CONSENT_TEXT,
    MARKETING_CONSENT_VERSION,
)
from app.schemas.booking import BookingRequest, ClientActor
from app.services.appointment_service import AppointmentService
from app.services.booking_service import BookingService
from app.services.client_payment_service import ClientPaymentService
from app.services.consent_service import ConsentService
from app.services.presentation_service import PresentationService
from app.services.reschedule_service import RescheduleService

_RESOURCE_PATH = re.compile(
    r"^/api/v1/(?P<resource>appointments|payments)/(?P<id>[1-9][0-9]{0,18})"
    r"(?:/(?P<action>cancel|reschedule|reschedule-options))?$"
)
_MAX_JSON_BODY_BYTES = 32_768


class _BookingPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service_id: int = Field(gt=0)
    window_id: int = Field(gt=0)
    staff_member_id: int | None = Field(default=None, gt=0)
    client_name: str = Field(min_length=1, max_length=255)
    phone: str = Field(max_length=32)
    client_comment: str | None = Field(default=None, max_length=2000)
    design_reference_id: int | None = Field(default=None, gt=0)
    reservation_token: str = Field(min_length=32, max_length=128)


class _CancellationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = Field(default=None, max_length=500)


class _ReschedulePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window_id: int = Field(gt=0)


class _MarketingConsentPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool


class MiniAppProductApi:
    """Transport adapter for authenticated client use cases."""

    def __init__(
        self,
        *,
        presentation: PresentationService,
        booking: BookingService,
        appointments: AppointmentService,
        reschedule: RescheduleService,
        consents: ConsentService,
        payments: ClientPaymentService,
    ) -> None:
        self._presentation = presentation
        self._booking = booking
        self._appointments = appointments
        self._reschedule = reschedule
        self._consents = consents
        self._payments = payments

    async def dispatch(
        self,
        request: HttpRequest,
        session: OpaqueSession,
        *,
        correlation_id: str,
    ) -> HttpResponse | None:
        try:
            return await self._dispatch(
                request,
                session,
                correlation_id=correlation_id,
            )
        except ValidationError as exc:
            raise validation_error_to_http(exc) from None

    async def _dispatch(
        self,
        request: HttpRequest,
        session: OpaqueSession,
        *,
        correlation_id: str,
    ) -> HttpResponse | None:
        actor = ClientActor(telegram_id=session.telegram_user_id)
        if request.path == "/api/v1/business":
            self._require_method(request, "GET")
            business = await self._presentation.get_business()
            return self._ok({"business": business.model_dump(mode="json")})
        if request.path == "/api/v1/services":
            self._require_method(request, "GET")
            services = await self._booking.list_active_services(actor)
            return self._ok({"services": [service.model_dump(mode="json") for service in services]})
        if request.path == "/api/v1/masters":
            self._require_method(request, "GET")
            query = self._query(request, required={"service_id"}, optional=set())
            options = await self._booking.list_bookable_masters(
                actor, self._positive_int(query["service_id"])
            )
            return self._ok(options.model_dump(mode="json"))
        if request.path == "/api/v1/availability/dates":
            self._require_method(request, "GET")
            query = self._query(
                request,
                required={"service_id"},
                optional={"staff_member_id"},
            )
            availability = await self._booking.list_availability(
                actor,
                self._positive_int(query["service_id"]),
                staff_member_id=self._optional_positive_int(query.get("staff_member_id")),
            )
            zone = ZoneInfo(availability.timezone)
            dates = sorted(
                {window.start_at.astimezone(zone).date() for window in availability.windows}
            )
            return self._ok(
                {
                    "timezone": availability.timezone,
                    "dates": [value.isoformat() for value in dates],
                }
            )
        if request.path == "/api/v1/availability/slots":
            self._require_method(request, "GET")
            query = self._query(
                request,
                required={"service_id", "date"},
                optional={"staff_member_id"},
            )
            availability = await self._booking.list_availability(
                actor,
                self._positive_int(query["service_id"]),
                staff_member_id=self._optional_positive_int(query.get("staff_member_id")),
                local_date=self._date(query["date"]),
            )
            return self._ok(availability.model_dump(mode="json"))
        if request.path == "/api/v1/reservations" or (
            request.path == "/api/v1/appointments" and request.method == "POST"
        ):
            self._require_method(request, "POST")
            values = await self._booking_request(request)
            receipt = await self._booking.book(
                actor,
                values,
                correlation_id=correlation_id,
            )
            return HttpResponse.json(201, receipt.model_dump(mode="json"))
        if request.path == "/api/v1/appointments":
            self._require_method(request, "GET")
            appointments = await self._appointments.list_my(actor)
            return self._ok(
                {
                    "appointments": [
                        appointment.model_dump(mode="json") for appointment in appointments
                    ]
                }
            )
        if request.path == "/api/v1/policies":
            self._require_method(request, "GET")
            business = await self._presentation.get_business()
            status = await self._consents.get_or_create_status(actor)
            return self._ok(
                {
                    "privacy": {
                        "url": business.privacy_policy_url,
                        "terms_url": business.terms_url,
                    },
                    "marketing": {
                        "version": MARKETING_CONSENT_VERSION,
                        "sha256": MARKETING_CONSENT_SHA256,
                        "text": MARKETING_CONSENT_TEXT,
                    },
                    "consents": status.model_dump(mode="json"),
                }
            )
        if request.path == "/api/v1/consents/privacy":
            self._require_method(request, "POST")
            await self._require_empty_json(request)
            status = await self._consents.accept_privacy(
                actor,
                correlation_id=correlation_id,
            )
            return self._ok(status.model_dump(mode="json"))
        if request.path == "/api/v1/consents/marketing":
            self._require_method(request, "POST")
            marketing_payload = _MarketingConsentPayload.model_validate(await self._json(request))
            status = await self._consents.set_marketing(
                actor,
                accepted=marketing_payload.accepted,
                source=ConsentSource.NOTIFICATION_SETTINGS,
                correlation_id=correlation_id,
            )
            return self._ok(status.model_dump(mode="json"))

        match = _RESOURCE_PATH.fullmatch(request.path)
        if match is None:
            return None
        resource_id = int(match.group("id"))
        resource = match.group("resource")
        action = match.group("action")
        if resource == "payments" and action is None:
            self._require_method(request, "GET")
            payment = await self._payments.get_my(actor, resource_id)
            return self._ok(payment.model_dump(mode="json"))
        if resource != "appointments":
            return None
        if action is None:
            self._require_method(request, "GET")
            appointment = await self._appointments.get_my(actor, resource_id)
            return self._ok(appointment.model_dump(mode="json"))
        if action == "cancel":
            self._require_method(request, "POST")
            cancellation_payload = _CancellationPayload.model_validate(await self._json(request))
            appointment = await self._appointments.cancel_my(
                actor,
                resource_id,
                reason=cancellation_payload.reason,
                correlation_id=correlation_id,
            )
            return self._ok(appointment.model_dump(mode="json"))
        if action == "reschedule-options":
            self._require_method(request, "GET")
            reschedule_options = await self._reschedule.list_my_options(actor, resource_id)
            return self._ok(reschedule_options.model_dump(mode="json"))
        if action == "reschedule":
            self._require_method(request, "POST")
            reschedule_payload = _ReschedulePayload.model_validate(await self._json(request))
            receipt = await self._reschedule.reschedule_my(
                actor,
                resource_id,
                reschedule_payload.window_id,
                correlation_id=correlation_id,
            )
            return self._ok(receipt.model_dump(mode="json"))
        return None

    async def _booking_request(self, request: HttpRequest) -> BookingRequest:
        key = request.headers.get("idempotency-key")
        if key is None:
            raise SafeHttpError(
                400,
                "idempotency_key_missing",
                "Idempotency-Key is required.",
            )
        payload = _BookingPayload.model_validate(await self._json(request))
        return BookingRequest.model_validate(
            {
                **payload.model_dump(),
                "checkout_idempotency_key": key,
            }
        )

    @staticmethod
    async def _require_empty_json(request: HttpRequest) -> None:
        payload = await MiniAppProductApi._json(request)
        if payload:
            raise SafeHttpError(400, "body_invalid", "Invalid request body.")

    @staticmethod
    async def _json(request: HttpRequest) -> dict[str, Any]:
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise SafeHttpError(415, "content_type_invalid", "JSON content is required.")
        body = await request.read_body(max_bytes=_MAX_JSON_BODY_BYTES)
        try:
            payload = json.loads(body, object_pairs_hook=_unique_object)
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise SafeHttpError(400, "json_invalid", "Invalid JSON body.") from None
        if not isinstance(payload, dict):
            raise SafeHttpError(400, "body_invalid", "Invalid request body.")
        return payload

    @staticmethod
    def _query(
        request: HttpRequest,
        *,
        required: set[str],
        optional: set[str],
    ) -> dict[str, str]:
        try:
            raw = request.query_string.decode("ascii")
            pairs = parse_qsl(
                raw,
                keep_blank_values=True,
                strict_parsing=True,
                max_num_fields=8,
            )
        except (UnicodeDecodeError, ValueError):
            raise SafeHttpError(400, "query_invalid", "Invalid query parameters.") from None
        result: dict[str, str] = {}
        allowed = required | optional
        for key, value in pairs:
            if key not in allowed or key in result:
                raise SafeHttpError(400, "query_invalid", "Invalid query parameters.")
            result[key] = value
        if not required.issubset(result):
            raise SafeHttpError(400, "query_invalid", "Invalid query parameters.")
        return result

    @staticmethod
    def _positive_int(value: str) -> int:
        if not value.isascii() or not value.isdecimal() or value.startswith("0"):
            raise SafeHttpError(400, "query_invalid", "Invalid query parameters.")
        parsed = int(value)
        if parsed > (1 << 63) - 1:
            raise SafeHttpError(400, "query_invalid", "Invalid query parameters.")
        return parsed

    @classmethod
    def _optional_positive_int(cls, value: str | None) -> int | None:
        return None if value is None else cls._positive_int(value)

    @staticmethod
    def _date(value: str) -> date:
        try:
            parsed = date.fromisoformat(value)
        except ValueError:
            raise SafeHttpError(400, "query_invalid", "Invalid query parameters.") from None
        if parsed.isoformat() != value:
            raise SafeHttpError(400, "query_invalid", "Invalid query parameters.")
        return parsed

    @staticmethod
    def _require_method(request: HttpRequest, expected: str) -> None:
        if request.method != expected:
            raise SafeHttpError(
                405,
                "method_not_allowed",
                "Method not allowed.",
                headers={"allow": expected},
            )

    @staticmethod
    def _ok(payload: dict[str, object]) -> HttpResponse:
        return HttpResponse.json(200, payload)


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def validation_error_to_http(error: ValidationError) -> SafeHttpError:
    """Collapse Pydantic details so submitted personal data is never reflected."""

    del error
    return SafeHttpError(400, "body_invalid", "Invalid request body.")
