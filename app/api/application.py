"""Pure-ASGI v1 API boundary with production security defaults."""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Collection, Mapping
from typing import Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from app.api.contracts import (
    AsgiReceive,
    AsgiScope,
    AsgiSend,
    HttpRequest,
    HttpResponse,
    LifecycleResource,
    ReadinessProbe,
    SafeHttpError,
    normalize_headers,
)
from app.api.rate_limit import HttpRateLimitError, HttpRateLimitPolicy, SharedHttpRateLimiter
from app.api.sessions import OpaqueSession, SessionStoreError
from app.api.telegram_auth import (
    ReplayStoreError,
    TelegramInitDataVerifier,
    VerifiedWebAppIdentity,
    WebAppAuthenticationError,
)
from app.api.webhooks import YooKassaWebhookBoundary
from app.domain.errors import (
    AppointmentNotFoundError,
    AuthorizationError,
    DomainError,
    EntityNotFoundError,
    PrivacyConsentRequiredError,
)
from app.logging import log_event, reset_correlation_id, set_correlation_id

logger = logging.getLogger(__name__)

_CORRELATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,63}$")
_HOST_PATTERN = re.compile(r"^[A-Za-z0-9.-]+(?::[0-9]{1,5})?$")
_CORS_REQUEST_HEADERS = frozenset(
    {
        "authorization",
        "content-type",
        "idempotency-key",
        "x-correlation-id",
        "x-telegram-init-data",
    }
)
_CORS_ALLOW_HEADERS = (
    "Authorization, Content-Type, Idempotency-Key, X-Correlation-ID, X-Telegram-Init-Data"
)


class MiniAppSessionIssuer(Protocol):
    """Shared application-service facade that exchanges a verified Telegram identity."""

    async def issue_session(
        self,
        identity: VerifiedWebAppIdentity,
        *,
        correlation_id: str,
    ) -> Mapping[str, object]: ...


class MiniAppSessionResolver(Protocol):
    """Resolve an opaque bearer token to its server-side Telegram identity."""

    async def resolve(self, token: str) -> OpaqueSession | None: ...


class MiniAppProductRouter(Protocol):
    """Authenticated v1 product route adapter."""

    async def dispatch(
        self,
        request: HttpRequest,
        session: OpaqueSession,
        *,
        correlation_id: str,
    ) -> HttpResponse | None: ...


class ApiApplication:
    """Minimal ASGI application; an external server is the only missing runtime adapter."""

    def __init__(
        self,
        *,
        allowed_hosts: Collection[str],
        allowed_origins: Collection[str],
        readiness_probe: ReadinessProbe,
        telegram_verifier: TelegramInitDataVerifier,
        session_issuer: MiniAppSessionIssuer,
        rate_limiter: SharedHttpRateLimiter,
        session_resolver: MiniAppSessionResolver | None = None,
        product_api: MiniAppProductRouter | None = None,
        yookassa_webhook: YooKassaWebhookBoundary | None = None,
        lifecycle_resources: Collection[LifecycleResource] = (),
        enforce_https: bool = True,
        max_body_bytes: int = 65_536,
        readiness_timeout_seconds: float = 3.0,
    ) -> None:
        self._allowed_hosts = self._validate_hosts(allowed_hosts)
        self._allowed_origins = self._validate_origins(allowed_origins)
        if not 1024 <= max_body_bytes <= 1_048_576:
            raise ValueError("max_body_bytes must be between 1024 and 1048576")
        if not 0.1 <= readiness_timeout_seconds <= 10:
            raise ValueError("readiness timeout must be between 0.1 and 10 seconds")
        self._readiness_probe = readiness_probe
        self._telegram_verifier = telegram_verifier
        self._session_issuer = session_issuer
        self._rate_limiter = rate_limiter
        self._session_resolver = session_resolver
        self._product_api = product_api
        self._yookassa_webhook = yookassa_webhook
        self._lifecycle_resources = tuple(lifecycle_resources)
        self._enforce_https = enforce_https
        self._max_body_bytes = max_body_bytes
        self._readiness_timeout_seconds = readiness_timeout_seconds

    async def __call__(
        self,
        scope: AsgiScope,
        receive: AsgiReceive,
        send: AsgiSend,
    ) -> None:
        scope_type = scope.get("type")
        if scope_type == "lifespan":
            await self._lifespan(receive, send)
            return
        if scope_type != "http":
            return

        correlation_id = uuid4().hex
        origin: str | None = None
        raw_scheme = scope.get("scheme", "http")
        scheme = raw_scheme if isinstance(raw_scheme, str) else "http"
        rate_request_id = uuid4().hex
        token = set_correlation_id(correlation_id)
        try:
            headers = normalize_headers(scope.get("headers", []))
            candidate = headers.get("x-correlation-id")
            if candidate is not None and _CORRELATION_ID_PATTERN.fullmatch(candidate):
                reset_correlation_id(token)
                correlation_id = candidate
                token = set_correlation_id(correlation_id)
            origin = headers.get("origin")
            request = self._request(scope, headers, receive)
            self._enforce_transport(request)
            self._enforce_origin(origin)
            response = await self._dispatch(
                request,
                correlation_id=correlation_id,
                rate_request_id=rate_request_id,
            )
        except SafeHttpError as exc:
            response = self._error_response(exc, correlation_id)
        except HttpRateLimitError as exc:
            unavailable = exc.code == "rate_limiter_unavailable"
            response = self._error_response(
                SafeHttpError(
                    503 if unavailable else 429,
                    exc.code,
                    ("Service temporarily unavailable." if unavailable else "Too many requests."),
                    headers={"retry-after": str(exc.retry_after_seconds)},
                ),
                correlation_id,
            )
        except ReplayStoreError:
            response = self._error_response(
                SafeHttpError(
                    503,
                    "authentication_store_unavailable",
                    "Service temporarily unavailable.",
                    headers={"retry-after": "5"},
                ),
                correlation_id,
            )
        except SessionStoreError:
            response = self._error_response(
                SafeHttpError(
                    503,
                    "session_store_unavailable",
                    "Service temporarily unavailable.",
                    headers={"retry-after": "5"},
                ),
                correlation_id,
            )
        except WebAppAuthenticationError:
            response = self._error_response(
                SafeHttpError(401, "telegram_authentication_failed", "Authentication failed."),
                correlation_id,
            )
        except (AppointmentNotFoundError, EntityNotFoundError):
            response = self._error_response(
                SafeHttpError(404, "resource_not_found", "Resource not found."),
                correlation_id,
            )
        except AuthorizationError:
            response = self._error_response(
                SafeHttpError(403, "operation_forbidden", "Operation is not allowed."),
                correlation_id,
            )
        except PrivacyConsentRequiredError:
            response = self._error_response(
                SafeHttpError(428, "privacy_consent_required", "Privacy consent is required."),
                correlation_id,
            )
        except DomainError:
            response = self._error_response(
                SafeHttpError(409, "operation_rejected", "Operation could not be completed."),
                correlation_id,
            )
        except Exception:
            # Never include request headers/body or exception text in the structured context.
            log_event(logger, logging.ERROR, "http.request_failed")
            response = self._error_response(
                SafeHttpError(500, "internal_error", "An unexpected error occurred."),
                correlation_id,
            )
        try:
            secured = self._secure_response(
                response,
                correlation_id=correlation_id,
                origin=origin,
                scheme=scheme,
            )
            await self._send_response(secured, send)
        finally:
            reset_correlation_id(token)

    async def _dispatch(
        self,
        request: HttpRequest,
        *,
        correlation_id: str,
        rate_request_id: str,
    ) -> HttpResponse:
        if request.path == "/health/live":
            self._require_method(request, "GET")
            return HttpResponse.json(200, {"status": "ok"})
        if request.path == "/health/ready":
            self._require_method(request, "GET")
            return await self._ready()
        if request.method == "OPTIONS":
            return self._preflight(request)
        if request.path == "/api/v1":
            self._require_method(request, "GET")
            await self._rate_limit(request, rate_request_id, "miniapp_api", 60, 60)
            return HttpResponse.json(200, {"api_version": "v1"})
        if request.path == "/api/v1/auth/telegram":
            self._require_method(request, "POST")
            self._require_empty_body(request)
            await self._rate_limit(request, rate_request_id, "miniapp_auth", 20, 60)
            raw_init_data = request.headers.get("x-telegram-init-data")
            if raw_init_data is None or raw_init_data != raw_init_data.strip():
                raise SafeHttpError(401, "telegram_init_data_missing", "Authentication failed.")
            identity = await self._telegram_verifier.verify_and_claim(raw_init_data)
            payload = await self._session_issuer.issue_session(
                identity,
                correlation_id=correlation_id,
            )
            return HttpResponse.json(200, payload)
        if request.path == "/api/v1/webhooks/yookassa":
            self._require_method(request, "POST")
            await self._rate_limit(request, rate_request_id, "payment_webhook", 120, 60)
            if self._yookassa_webhook is None:
                raise SafeHttpError(404, "route_not_found", "Resource not found.")
            body = await request.read_body(max_bytes=self._max_body_bytes)
            disposition = await self._yookassa_webhook.handle(
                body,
                content_type=request.headers.get("content-type", ""),
                correlation_id=correlation_id,
            )
            return HttpResponse.json(
                202,
                {"accepted": True, "duplicate": disposition.duplicate},
            )
        if request.path.startswith("/api/v1/") and self._product_api is not None:
            session = await self._resolve_session(request)
            await self._rate_limit(
                request,
                rate_request_id,
                "miniapp_product",
                120,
                60,
                subject=f"telegram:{session.telegram_user_id}",
            )
            response = await self._product_api.dispatch(
                request,
                session,
                correlation_id=correlation_id,
            )
            if response is not None:
                return response
        raise SafeHttpError(404, "route_not_found", "Resource not found.")

    async def _resolve_session(self, request: HttpRequest) -> OpaqueSession:
        if self._session_resolver is None:
            raise SafeHttpError(404, "route_not_found", "Resource not found.")
        authorization = request.headers.get("authorization")
        if authorization is None or not authorization.startswith("Bearer "):
            raise SafeHttpError(401, "session_missing", "Authentication failed.")
        token = authorization.removeprefix("Bearer ")
        if not token or token != token.strip():
            raise SafeHttpError(401, "session_invalid", "Authentication failed.")
        try:
            session = await self._session_resolver.resolve(token)
        except ValueError:
            raise SafeHttpError(401, "session_invalid", "Authentication failed.") from None
        if session is None:
            raise SafeHttpError(401, "session_expired", "Authentication failed.")
        return session

    async def _ready(self) -> HttpResponse:
        try:
            report = await asyncio.wait_for(
                self._readiness_probe.check(),
                timeout=self._readiness_timeout_seconds,
            )
        except Exception:
            log_event(logger, logging.WARNING, "http.readiness_failed")
            return HttpResponse.json(
                503,
                {"status": "unavailable", "checks": {"dependencies": False}},
            )
        return HttpResponse.json(
            200 if report.ready else 503,
            {
                "status": "ready" if report.ready else "unavailable",
                "checks": dict(report.checks),
            },
        )

    async def _rate_limit(
        self,
        request: HttpRequest,
        rate_request_id: str,
        scope: str,
        limit: int,
        window_seconds: int,
        *,
        subject: str | None = None,
    ) -> None:
        await self._rate_limiter.enforce(
            subject=subject or request.client_host,
            request_id=rate_request_id,
            policy=HttpRateLimitPolicy(scope, limit, window_seconds),
        )

    def _preflight(self, request: HttpRequest) -> HttpResponse:
        origin = request.headers.get("origin")
        requested_method = request.headers.get("access-control-request-method", "").upper()
        raw_requested_headers = request.headers.get("access-control-request-headers", "")
        requested_headers = {
            value.strip().lower() for value in raw_requested_headers.split(",") if value.strip()
        }
        if (
            origin is None
            or requested_method not in {"GET", "POST"}
            or not requested_headers.issubset(_CORS_REQUEST_HEADERS)
            or not request.path.startswith("/api/v1")
        ):
            raise SafeHttpError(403, "cors_preflight_rejected", "Request is not allowed.")
        return HttpResponse.empty(
            204,
            headers={
                "access-control-allow-methods": "GET, POST, OPTIONS",
                "access-control-allow-headers": _CORS_ALLOW_HEADERS,
                "access-control-max-age": "300",
            },
        )

    def _request(
        self,
        scope: AsgiScope,
        headers: Mapping[str, str],
        receive: AsgiReceive,
    ) -> HttpRequest:
        method = self._safe_scope_string(scope, "method").upper()
        path = self._safe_scope_string(scope, "path")
        scheme = self._safe_scope_string(scope, "scheme", default="http").lower()
        if not method.isascii() or not method.isalpha() or len(method) > 16:
            raise SafeHttpError(400, "method_invalid", "Invalid request.")
        if not path.startswith("/") or len(path) > 2048 or "\x00" in path:
            raise SafeHttpError(400, "path_invalid", "Invalid request.")
        raw_content_length = headers.get("content-length")
        if raw_content_length is not None:
            if (
                len(raw_content_length) > 10
                or not raw_content_length.isascii()
                or not raw_content_length.isdecimal()
            ):
                raise SafeHttpError(400, "content_length_invalid", "Invalid request.")
            if int(raw_content_length) > self._max_body_bytes:
                raise SafeHttpError(413, "body_too_large", "Request body is too large.")
        client_host = "unknown"
        raw_client = scope.get("client")
        if isinstance(raw_client, (tuple, list)) and raw_client and isinstance(raw_client[0], str):
            client_host = raw_client[0]
        raw_query = scope.get("query_string", b"")
        if not isinstance(raw_query, bytes) or len(raw_query) > 4096:
            raise SafeHttpError(400, "query_invalid", "Invalid query parameters.")
        return HttpRequest(
            method=method,
            path=path,
            scheme=scheme,
            headers=headers,
            client_host=client_host,
            receive=receive,
            query_string=raw_query,
        )

    def _enforce_transport(self, request: HttpRequest) -> None:
        host = request.headers.get("host")
        if host is None or host.lower() not in self._allowed_hosts:
            raise SafeHttpError(400, "host_not_allowed", "Invalid request.")
        if self._enforce_https and request.path.startswith("/api/") and request.scheme != "https":
            raise SafeHttpError(426, "https_required", "HTTPS is required.")

    def _enforce_origin(self, origin: str | None) -> None:
        if origin is not None and origin not in self._allowed_origins:
            raise SafeHttpError(403, "origin_not_allowed", "Request is not allowed.")

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
    def _require_empty_body(request: HttpRequest) -> None:
        content_length = request.headers.get("content-length")
        if content_length not in (None, "0"):
            raise SafeHttpError(400, "body_not_allowed", "Invalid request.")
        if "transfer-encoding" in request.headers:
            raise SafeHttpError(400, "body_not_allowed", "Invalid request.")

    @staticmethod
    def _error_response(error: SafeHttpError, correlation_id: str) -> HttpResponse:
        return HttpResponse.json(
            error.status_code,
            {
                "error": {"code": error.code, "message": error.safe_message},
                "correlation_id": correlation_id,
            },
            headers=error.headers,
        )

    def _secure_response(
        self,
        response: HttpResponse,
        *,
        correlation_id: str,
        origin: str | None,
        scheme: str,
    ) -> HttpResponse:
        headers = dict(response.headers)
        headers.update(
            {
                "cache-control": "no-store",
                "content-security-policy": "default-src 'none'; frame-ancestors 'none'",
                "cross-origin-resource-policy": "same-site",
                "permissions-policy": "camera=(), microphone=(), geolocation=()",
                "referrer-policy": "no-referrer",
                "x-content-type-options": "nosniff",
                "x-correlation-id": correlation_id,
                "x-frame-options": "DENY",
            }
        )
        if scheme == "https":
            headers["strict-transport-security"] = "max-age=31536000; includeSubDomains"
        if origin is not None and origin in self._allowed_origins:
            headers["access-control-allow-origin"] = origin
            headers["vary"] = "Origin"
        return HttpResponse(response.status_code, response.body, tuple(headers.items()))

    @staticmethod
    async def _send_response(response: HttpResponse, send: AsgiSend) -> None:
        headers: list[tuple[bytes, bytes]] = []
        for name, value in response.headers:
            if "\r" in name or "\n" in name or "\r" in value or "\n" in value:
                raise RuntimeError("unsafe response header")
            headers.append((name.encode("ascii"), value.encode("latin-1")))
        await send(
            {
                "type": "http.response.start",
                "status": response.status_code,
                "headers": headers,
            }
        )
        await send({"type": "http.response.body", "body": response.body, "more_body": False})

    async def _lifespan(self, receive: AsgiReceive, send: AsgiSend) -> None:
        started: list[LifecycleResource] = []
        while True:
            message = await receive()
            message_type = message.get("type")
            if message_type == "lifespan.startup":
                try:
                    for resource in self._lifecycle_resources:
                        started.append(resource)
                        await resource.start()
                except Exception:
                    await self._close_resources(started)
                    log_event(logger, logging.ERROR, "http.startup_failed")
                    await send(
                        {
                            "type": "lifespan.startup.failed",
                            "message": "Application startup failed.",
                        }
                    )
                    return
                await send({"type": "lifespan.startup.complete"})
            elif message_type == "lifespan.shutdown":
                closed = await self._close_resources(started)
                if closed:
                    await send({"type": "lifespan.shutdown.complete"})
                else:
                    await send(
                        {
                            "type": "lifespan.shutdown.failed",
                            "message": "Application shutdown failed.",
                        }
                    )
                return

    @staticmethod
    async def _close_resources(resources: Collection[LifecycleResource]) -> bool:
        closed = True
        for resource in reversed(tuple(resources)):
            try:
                await resource.close()
            except Exception:
                closed = False
                log_event(logger, logging.ERROR, "http.shutdown_resource_failed")
        return closed

    @staticmethod
    def _safe_scope_string(scope: AsgiScope, key: str, *, default: str = "") -> str:
        value = scope.get(key, default)
        if not isinstance(value, str):
            raise SafeHttpError(400, "scope_invalid", "Invalid request.")
        return value

    @staticmethod
    def _validate_hosts(values: Collection[str]) -> frozenset[str]:
        hosts = frozenset(value.strip().lower() for value in values)
        if not hosts or any(
            not value or value == "*" or _HOST_PATTERN.fullmatch(value) is None for value in hosts
        ):
            raise ValueError("allowed_hosts must contain explicit DNS hosts with optional ports")
        return hosts

    @staticmethod
    def _validate_origins(values: Collection[str]) -> frozenset[str]:
        origins = frozenset(value.strip() for value in values)
        if not origins:
            raise ValueError("at least one explicit Mini App origin is required")
        for origin in origins:
            parsed = urlsplit(origin)
            canonical = f"{parsed.scheme}://{parsed.netloc}"
            if (
                not origin.isascii()
                or parsed.scheme != "https"
                or parsed.hostname is None
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path not in {"", "/"}
                or parsed.query
                or parsed.fragment
                or origin.rstrip("/") != canonical
            ):
                raise ValueError("allowed origins must be explicit HTTPS origins")
        return frozenset(origin.rstrip("/") for origin in origins)
