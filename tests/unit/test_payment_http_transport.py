"""Security and resource-boundary tests for the concrete payment HTTP transport."""

from __future__ import annotations

import ssl
from collections.abc import AsyncIterator
from typing import Any, cast

import aiohttp
import pytest
from pydantic import SecretStr

from app.payments.http_transport import AioHttpTransport
from app.payments.providers.base import (
    HttpBasicAuth,
    HttpRequest,
    PaymentProviderProtocolError,
    PaymentProviderUnavailableError,
)


class FakeContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks
        self.iterated = False

    async def iter_chunked(self, size: int) -> AsyncIterator[bytes]:
        assert size > 0
        self.iterated = True
        for chunk in self._chunks:
            yield chunk


class FakeResponse:
    def __init__(
        self,
        body_chunks: list[bytes],
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self.content = FakeContent(body_chunks)

    async def __aenter__(self) -> FakeResponse:
        return self

    async def __aexit__(self, *args: object) -> None:
        del args


class FakeSession:
    def __init__(self, response: FakeResponse, init_kwargs: dict[str, object]) -> None:
        self.closed = False
        self.response = response
        self.init_kwargs = init_kwargs
        self.requests: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def request(self, *args: object, **kwargs: object) -> FakeResponse:
        self.requests.append((args, kwargs))
        return self.response

    async def close(self) -> None:
        self.closed = True


def install_fake_session(
    monkeypatch: pytest.MonkeyPatch,
    response: FakeResponse,
) -> list[FakeSession]:
    sessions: list[FakeSession] = []

    def factory(**kwargs: Any) -> FakeSession:
        session = FakeSession(response, kwargs)
        sessions.append(session)
        return session

    monkeypatch.setattr("app.payments.http_transport.aiohttp.ClientSession", factory)
    return sessions


@pytest.mark.asyncio
async def test_transport_enforces_safe_aiohttp_options_and_decodes_object(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = FakeResponse(
        [b'{"id":"payment-1",', b'"status":"pending"}'],
        headers={"Content-Length": "37"},
    )
    sessions = install_fake_session(monkeypatch, response)
    transport = AioHttpTransport(connect_timeout_seconds=2, total_timeout_seconds=8)

    async with transport:
        result = await transport.request(
            HttpRequest(
                method="GET",
                url="https://api.yookassa.ru/v3/payments/payment-1",
                basic_auth=HttpBasicAuth("shop", SecretStr("top-secret")),
                timeout_seconds=20,
            )
        )

    assert result.status_code == 200
    assert result.json_body == {"id": "payment-1", "status": "pending"}
    assert sessions[0].init_kwargs["trust_env"] is False
    assert sessions[0].closed
    args, kwargs = sessions[0].requests[0]
    assert args[:2] == ("GET", "https://api.yookassa.ru/v3/payments/payment-1")
    assert kwargs["allow_redirects"] is False
    assert kwargs["proxy"] is None
    ssl_context = cast(ssl.SSLContext, kwargs["ssl"])
    timeout = cast(aiohttp.ClientTimeout, kwargs["timeout"])
    assert ssl_context.check_hostname is True
    assert timeout.total == 8
    assert "top-secret" not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://api.yookassa.ru/v3/payments/x",
        "https://evil.example/v3/payments/x",
        "https://user:secret@api.yookassa.ru/v3/payments/x",
        "https://api.yookassa.ru:8443/v3/payments/x",
        "https://api.yookassa.ru/v3/payments/x#fragment",
    ],
)
async def test_transport_rejects_non_tls_or_non_allowlisted_destinations(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    sessions = install_fake_session(monkeypatch, FakeResponse([b"{}"]))
    transport = AioHttpTransport()

    async with transport:
        with pytest.raises(PaymentProviderProtocolError, match="url_not_allowed"):
            await transport.request(HttpRequest(method="GET", url=url))

    assert not sessions[0].requests


@pytest.mark.asyncio
async def test_declared_response_limit_is_checked_before_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = FakeResponse([b"{}"], headers={"Content-Length": "999999"})
    install_fake_session(monkeypatch, response)
    transport = AioHttpTransport(max_response_bytes=1024)

    async with transport:
        with pytest.raises(PaymentProviderProtocolError, match="response_too_large"):
            await transport.request(
                HttpRequest(method="GET", url="https://api.yookassa.ru/v3/payments/x")
            )

    assert not response.content.iterated


@pytest.mark.asyncio
async def test_streamed_response_limit_is_checked_after_decompression_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = FakeResponse([b'{"payload":"', b"a" * 30, b'"}'])
    install_fake_session(monkeypatch, response)
    transport = AioHttpTransport()

    async with transport:
        with pytest.raises(PaymentProviderProtocolError, match="response_too_large"):
            await transport.request(
                HttpRequest(
                    method="GET",
                    url="https://api.yookassa.ru/v3/payments/x",
                    max_response_bytes=20,
                )
            )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body,error_code",
    [
        (b"[]", "json_object_required"),
        (b'{"status":"ok","status":"bad"}', "json_invalid"),
        (b'{"amount":NaN}', "json_invalid"),
        (b"not-json", "json_invalid"),
    ],
)
async def test_only_finite_json_objects_with_unique_keys_are_accepted(
    monkeypatch: pytest.MonkeyPatch,
    body: bytes,
    error_code: str,
) -> None:
    install_fake_session(monkeypatch, FakeResponse([body]))
    transport = AioHttpTransport()

    async with transport:
        with pytest.raises(PaymentProviderProtocolError, match=error_code):
            await transport.request(
                HttpRequest(method="GET", url="https://api.yookassa.ru/v3/payments/x")
            )


@pytest.mark.asyncio
async def test_transport_requires_explicit_lifecycle_and_safe_errors() -> None:
    transport = AioHttpTransport()

    with pytest.raises(PaymentProviderUnavailableError) as raised:
        await transport.request(
            HttpRequest(
                method="GET",
                url="https://api.yookassa.ru/v3/payments/secret-provider-id",
            )
        )

    assert "secret-provider-id" not in str(raised.value)


@pytest.mark.asyncio
async def test_transport_rejects_ambiguous_hop_by_hop_or_auth_headers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sessions = install_fake_session(monkeypatch, FakeResponse([b"{}"]))
    transport = AioHttpTransport()

    async with transport:
        with pytest.raises(PaymentProviderProtocolError, match="headers_invalid"):
            await transport.request(
                HttpRequest(
                    method="GET",
                    url="https://api.yookassa.ru/v3/payments/x",
                    headers={"Host": "evil.example", "Authorization": "secret"},
                )
            )

    assert not sessions[0].requests


@pytest.mark.parametrize("host", ["*", "bad host", "-invalid.example", "example..test"])
def test_transport_requires_exact_valid_dns_allowlist(host: str) -> None:
    with pytest.raises(ValueError, match="exact ASCII"):
        AioHttpTransport(allowed_hosts={host})
