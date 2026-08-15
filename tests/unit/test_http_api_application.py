"""Direct ASGI tests for the versioned, hardened Mini App boundary."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.api.application import (
    ApiApplication,
    MiniAppProductRouter,
    MiniAppSessionIssuer,
    MiniAppSessionResolver,
)
from app.api.contracts import AsgiMessage, LifecycleResource, ReadinessProbe, ReadinessReport
from app.api.rate_limit import HttpRateLimitError, SharedHttpRateLimiter
from app.api.sessions import OpaqueSession, SessionStoreError
from app.api.telegram_auth import (
    TelegramInitDataVerifier,
    VerifiedWebAppIdentity,
    WebAppAuthenticationError,
)
from app.api.webhooks import WebhookDisposition, YooKassaWebhookBoundary

IDENTITY = VerifiedWebAppIdentity(
    telegram_user_id=123_456,
    auth_date=datetime(2026, 8, 10, 12, tzinfo=UTC),
    session_fingerprint="a" * 64,
)


def build_app(
    *,
    ready: bool = True,
    max_body_bytes: int = 65_536,
    lifecycle_resources: tuple[LifecycleResource, ...] = (),
    session_resolver: MiniAppSessionResolver | None = None,
    product_api: MiniAppProductRouter | None = None,
) -> tuple[ApiApplication, MagicMock, MagicMock, MagicMock, MagicMock]:
    probe = MagicMock()
    probe.check = AsyncMock(
        return_value=ReadinessReport(
            ready=ready,
            checks={"database": ready, "redis": ready, "workers": ready},
        )
    )
    verifier = MagicMock()
    verifier.verify_and_claim = AsyncMock(return_value=IDENTITY)
    issuer = MagicMock()
    issuer.issue_session = AsyncMock(
        return_value={"session_token": "opaque-server-session", "expires_in": 300}
    )
    limiter = MagicMock()
    limiter.enforce = AsyncMock()
    webhook = MagicMock()
    webhook.handle = AsyncMock(return_value=WebhookDisposition(duplicate=False))
    app = ApiApplication(
        allowed_hosts={"api.example.test"},
        allowed_origins={"https://mini.example.test"},
        readiness_probe=cast(ReadinessProbe, probe),
        telegram_verifier=cast(TelegramInitDataVerifier, verifier),
        session_issuer=cast(MiniAppSessionIssuer, issuer),
        rate_limiter=cast(SharedHttpRateLimiter, limiter),
        session_resolver=session_resolver,
        product_api=product_api,
        yookassa_webhook=cast(YooKassaWebhookBoundary, webhook),
        max_body_bytes=max_body_bytes,
        lifecycle_resources=lifecycle_resources,
    )
    return app, probe, verifier, issuer, limiter


async def request(
    app: ApiApplication,
    *,
    method: str,
    path: str,
    scheme: str = "https",
    headers: Mapping[str, str] | None = None,
    chunks: list[bytes] | None = None,
) -> tuple[int, dict[str, str], dict[str, object] | None]:
    raw_headers = {"host": "api.example.test", **dict(headers or {})}
    body_chunks = list(chunks if chunks is not None else [b""])
    incoming: list[AsgiMessage] = [
        {
            "type": "http.request",
            "body": value,
            "more_body": index < len(body_chunks) - 1,
        }
        for index, value in enumerate(body_chunks)
    ]
    sent: list[AsgiMessage] = []

    async def receive() -> AsgiMessage:
        if incoming:
            return incoming.pop(0)
        return {"type": "http.disconnect"}

    async def send(message: AsgiMessage) -> None:
        sent.append(message)

    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "scheme": scheme,
            "method": method,
            "path": path,
            "query_string": b"",
            "headers": [
                (name.encode("ascii"), value.encode("latin-1"))
                for name, value in raw_headers.items()
            ],
            "client": ("203.0.113.42", 54321),
        },
        receive,
        send,
    )
    start, body = sent
    response_headers = {
        name.decode("ascii"): value.decode("latin-1") for name, value in start["headers"]
    }
    raw_body = body["body"]
    payload = json.loads(raw_body) if raw_body else None
    return int(start["status"]), response_headers, payload


@pytest.mark.asyncio
async def test_liveness_has_security_headers_and_propagates_safe_correlation_id() -> None:
    app, _, _, _, limiter = build_app()

    status, headers, payload = await request(
        app,
        method="GET",
        path="/health/live",
        headers={"x-correlation-id": "request-12345678"},
    )

    assert status == 200
    assert payload == {"status": "ok"}
    assert headers["x-correlation-id"] == "request-12345678"
    assert headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"
    assert headers["strict-transport-security"].startswith("max-age=")
    assert headers["cache-control"] == "no-store"
    limiter.enforce.assert_not_awaited()


@pytest.mark.asyncio
async def test_readiness_returns_only_boolean_component_state() -> None:
    app, probe, _, _, _ = build_app(ready=False)

    status, _, payload = await request(app, method="GET", path="/health/ready")

    assert status == 503
    assert payload == {
        "status": "unavailable",
        "checks": {"database": False, "redis": False, "workers": False},
    }
    probe.check.assert_awaited_once()


@pytest.mark.asyncio
async def test_trusted_host_and_https_are_enforced_before_api_services() -> None:
    app, _, verifier, _, limiter = build_app()

    host_status, _, _ = await request(
        app,
        method="POST",
        path="/api/v1/auth/telegram",
        headers={"host": "evil.example.test", "x-telegram-init-data": "secret-init-data"},
    )
    https_status, _, https_payload = await request(
        app,
        method="GET",
        path="/api/v1",
        scheme="http",
    )

    assert host_status == 400
    assert https_status == 426
    assert https_payload is not None
    assert https_payload["error"]["code"] == "https_required"  # type: ignore[index]
    verifier.verify_and_claim.assert_not_awaited()
    limiter.enforce.assert_not_awaited()


@pytest.mark.asyncio
async def test_allowed_origin_auth_exchanges_only_raw_init_data_for_server_session() -> None:
    app, _, verifier, issuer, limiter = build_app()
    raw = "query_id=signed-and-opaque&hash=not-logged"

    status, headers, payload = await request(
        app,
        method="POST",
        path="/api/v1/auth/telegram",
        headers={
            "content-length": "0",
            "origin": "https://mini.example.test",
            "x-correlation-id": "request-12345678",
            "x-telegram-init-data": raw,
        },
    )

    assert status == 200
    assert payload == {"session_token": "opaque-server-session", "expires_in": 300}
    assert headers["access-control-allow-origin"] == "https://mini.example.test"
    assert "access-control-allow-credentials" not in headers
    assert raw not in json.dumps(payload)
    verifier.verify_and_claim.assert_awaited_once_with(raw)
    issuer.issue_session.assert_awaited_once_with(
        IDENTITY,
        correlation_id="request-12345678",
    )
    limiter.enforce.assert_awaited_once()


@pytest.mark.asyncio
async def test_unknown_origin_is_rejected_before_authentication() -> None:
    app, _, verifier, _, _ = build_app()

    status, headers, payload = await request(
        app,
        method="POST",
        path="/api/v1/auth/telegram",
        headers={
            "content-length": "0",
            "origin": "https://evil.example.test",
            "x-telegram-init-data": "raw-secret",
        },
    )

    assert status == 403
    assert payload is not None
    assert payload["error"]["code"] == "origin_not_allowed"  # type: ignore[index]
    assert "access-control-allow-origin" not in headers
    verifier.verify_and_claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_preflight_is_allowlisted_and_never_enables_credentials() -> None:
    app, _, _, _, limiter = build_app()

    status, headers, payload = await request(
        app,
        method="OPTIONS",
        path="/api/v1/auth/telegram",
        headers={
            "origin": "https://mini.example.test",
            "access-control-request-method": "POST",
            "access-control-request-headers": "X-Telegram-Init-Data, X-Correlation-ID",
        },
    )

    assert status == 204
    assert payload is None
    assert headers["access-control-allow-methods"] == "GET, POST, OPTIONS"
    assert "access-control-allow-credentials" not in headers
    limiter.enforce.assert_not_awaited()


@pytest.mark.asyncio
async def test_auth_error_and_unexpected_error_are_generic(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app, _, verifier, issuer, _ = build_app()
    verifier.verify_and_claim.side_effect = WebAppAuthenticationError("specific_private_reason")

    auth_status, _, auth_payload = await request(
        app,
        method="POST",
        path="/api/v1/auth/telegram",
        headers={"content-length": "0", "x-telegram-init-data": "private-raw-data"},
    )

    assert auth_status == 401
    assert auth_payload is not None
    assert "specific_private_reason" not in json.dumps(auth_payload)
    assert "private-raw-data" not in json.dumps(auth_payload)

    verifier.verify_and_claim.side_effect = None
    verifier.verify_and_claim.return_value = IDENTITY
    issuer.issue_session.side_effect = RuntimeError("database password is super-secret")
    with caplog.at_level(logging.ERROR):
        error_status, _, error_payload = await request(
            app,
            method="POST",
            path="/api/v1/auth/telegram",
            headers={"content-length": "0", "x-telegram-init-data": "signed-data"},
        )
    assert error_status == 500
    assert error_payload is not None
    assert "super-secret" not in json.dumps(error_payload)
    assert "super-secret" not in caplog.text


@pytest.mark.asyncio
async def test_session_store_outage_fails_exchange_closed_with_retryable_503() -> None:
    app, _, _, issuer, _ = build_app()
    issuer.issue_session.side_effect = SessionStoreError()

    status, headers, payload = await request(
        app,
        method="POST",
        path="/api/v1/auth/telegram",
        headers={"content-length": "0", "x-telegram-init-data": "signed-data"},
    )

    assert status == 503
    assert headers["retry-after"] == "5"
    assert payload is not None
    assert payload["error"]["code"] == "session_store_unavailable"  # type: ignore[index]


@pytest.mark.asyncio
async def test_rate_limit_rejection_maps_to_429_with_retry_after() -> None:
    app, _, verifier, _, limiter = build_app()
    limiter.enforce.side_effect = HttpRateLimitError(
        "rate_limit_exceeded",
        retry_after_seconds=19,
    )

    status, headers, payload = await request(app, method="GET", path="/api/v1")

    assert status == 429
    assert headers["retry-after"] == "19"
    assert payload is not None
    assert payload["error"]["code"] == "rate_limit_exceeded"  # type: ignore[index]
    verifier.verify_and_claim.assert_not_awaited()


@pytest.mark.asyncio
async def test_client_correlation_reuse_cannot_bypass_rate_limit_consumption() -> None:
    app, _, _, _, limiter = build_app()
    headers = {"x-correlation-id": "same-request-1234"}

    await request(app, method="GET", path="/api/v1", headers=headers)
    await request(app, method="GET", path="/api/v1", headers=headers)

    first_request_id = limiter.enforce.await_args_list[0].kwargs["request_id"]
    second_request_id = limiter.enforce.await_args_list[1].kwargs["request_id"]
    assert first_request_id != second_request_id
    assert first_request_id != "same-request-1234"


@pytest.mark.asyncio
async def test_product_routes_require_and_resolve_opaque_bearer_session() -> None:
    resolver = MagicMock()
    resolver.resolve = AsyncMock(
        return_value=OpaqueSession(
            telegram_user_id=IDENTITY.telegram_user_id,
            issued_at=IDENTITY.auth_date,
            expires_at=IDENTITY.auth_date.replace(minute=10),
            auth_date=IDENTITY.auth_date,
        )
    )
    product = MagicMock()
    product.dispatch = AsyncMock(
        return_value=MagicMock(
            status_code=200,
            body=b'{"business":{"id":1}}',
            headers=(("content-type", "application/json"),),
        )
    )
    app, _, _, _, limiter = build_app(
        session_resolver=cast(MiniAppSessionResolver, resolver),
        product_api=cast(MiniAppProductRouter, product),
    )

    missing_status, _, missing_payload = await request(
        app,
        method="GET",
        path="/api/v1/business",
    )
    status, _, _ = await request(
        app,
        method="GET",
        path="/api/v1/business",
        headers={"authorization": f"Bearer {'a' * 43}"},
    )

    assert missing_status == 401
    assert missing_payload is not None
    assert missing_payload["error"]["code"] == "session_missing"  # type: ignore[index]
    assert status == 200
    resolver.resolve.assert_awaited_once_with("a" * 43)
    session = product.dispatch.await_args.args[1]
    assert session.telegram_user_id == IDENTITY.telegram_user_id
    assert limiter.enforce.await_args.kwargs["subject"] == "telegram:123456"


@pytest.mark.asyncio
async def test_chunked_webhook_body_is_rejected_at_configured_boundary() -> None:
    app, _, _, _, _ = build_app(max_body_bytes=1024)
    webhook = app._yookassa_webhook
    assert webhook is not None

    status, _, payload = await request(
        app,
        method="POST",
        path="/api/v1/webhooks/yookassa",
        headers={"content-type": "application/json"},
        chunks=[b"x" * 800, b"y" * 300],
    )

    assert status == 413
    assert payload is not None
    assert payload["error"]["code"] == "body_too_large"  # type: ignore[index]
    cast(MagicMock, webhook).handle.assert_not_awaited()


@pytest.mark.asyncio
async def test_asgi_lifespan_owns_transport_resources() -> None:
    resource = MagicMock()
    resource.start = AsyncMock()
    resource.close = AsyncMock()
    app, _, _, _, _ = build_app(
        lifecycle_resources=(cast(LifecycleResource, resource),),
    )
    incoming: list[AsgiMessage] = [
        {"type": "lifespan.startup"},
        {"type": "lifespan.shutdown"},
    ]
    sent: list[AsgiMessage] = []

    async def receive() -> AsgiMessage:
        return incoming.pop(0)

    async def send(message: AsgiMessage) -> None:
        sent.append(message)

    await app({"type": "lifespan"}, receive, send)

    resource.start.assert_awaited_once()
    resource.close.assert_awaited_once()
    assert sent == [
        {"type": "lifespan.startup.complete"},
        {"type": "lifespan.shutdown.complete"},
    ]


@pytest.mark.asyncio
async def test_lifespan_failure_closes_started_resources_without_secret_details() -> None:
    first = MagicMock()
    first.start = AsyncMock()
    first.close = AsyncMock()
    failing = MagicMock()
    failing.start = AsyncMock(side_effect=OSError("redis://user:secret@example"))
    failing.close = AsyncMock()
    app, _, _, _, _ = build_app(
        lifecycle_resources=(
            cast(LifecycleResource, first),
            cast(LifecycleResource, failing),
        ),
    )
    sent: list[AsgiMessage] = []

    async def receive() -> AsgiMessage:
        return {"type": "lifespan.startup"}

    async def send(message: AsgiMessage) -> None:
        sent.append(message)

    await app({"type": "lifespan"}, receive, send)

    first.close.assert_awaited_once()
    failing.close.assert_awaited_once()
    assert sent == [
        {
            "type": "lifespan.startup.failed",
            "message": "Application startup failed.",
        }
    ]
    assert "secret" not in repr(sent)
