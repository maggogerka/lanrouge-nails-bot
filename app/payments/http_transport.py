"""Bounded aiohttp transport for payment-provider calls.

The transport deliberately exposes no response bytes, credentials, URLs, or
downstream exception text through errors or logs.  It must be started and
closed by the process composition root.
"""

from __future__ import annotations

import asyncio
import json
import re
import ssl
from collections.abc import Iterable, Mapping
from types import TracebackType
from urllib.parse import urlsplit

import aiohttp

from app.payments.providers.base import (
    HttpRequest,
    HttpResponse,
    PaymentProviderError,
    PaymentProviderProtocolError,
    PaymentProviderUnavailableError,
)

_DEFAULT_ALLOWED_HOSTS = frozenset({"api.yookassa.ru"})
_MAX_CONFIGURED_RESPONSE_BYTES = 1_048_576
_READ_CHUNK_BYTES = 8192
_DNS_HOST = re.compile(
    r"^(?=.{1,253}\Z)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)*"
    r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z"
)
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_FORBIDDEN_HEADERS = frozenset(
    {
        "authorization",
        "connection",
        "content-length",
        "cookie",
        "host",
        "proxy-authorization",
        "transfer-encoding",
    }
)


class _DuplicateJsonKeyError(ValueError):
    pass


def _unique_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateJsonKeyError
        result[key] = value
    return result


def _reject_json_constant(value: str) -> object:
    del value
    raise ValueError("non-finite JSON number")


class AioHttpTransport:
    """Reusable TLS-only transport with strict time, host, and size bounds."""

    def __init__(
        self,
        *,
        allowed_hosts: Iterable[str] = _DEFAULT_ALLOWED_HOSTS,
        connect_timeout_seconds: float = 3.0,
        total_timeout_seconds: float = 10.0,
        max_response_bytes: int = 65_536,
    ) -> None:
        hosts = frozenset(host.strip().casefold() for host in allowed_hosts)
        if not hosts or any(_DNS_HOST.fullmatch(host) is None for host in hosts):
            raise ValueError("allowed_hosts must contain exact ASCII hostnames")
        if not 0 < connect_timeout_seconds <= total_timeout_seconds <= 120:
            raise ValueError("HTTP timeouts must satisfy 0 < connect <= total <= 120")
        if not 1024 <= max_response_bytes <= _MAX_CONFIGURED_RESPONSE_BYTES:
            raise ValueError("max_response_bytes must be between 1024 and 1048576")

        self._allowed_hosts = hosts
        self._connect_timeout_seconds = connect_timeout_seconds
        self._total_timeout_seconds = total_timeout_seconds
        self._max_response_bytes = max_response_bytes
        self._session: aiohttp.ClientSession | None = None
        self._lifecycle_lock = asyncio.Lock()
        self._ssl_context = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)

    async def __aenter__(self) -> AioHttpTransport:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc_value, traceback
        await self.close()

    async def start(self) -> None:
        """Create the connection pool inside the running event loop."""

        async with self._lifecycle_lock:
            if self._session is not None and not self._session.closed:
                return
            timeout = aiohttp.ClientTimeout(
                total=self._total_timeout_seconds,
                connect=self._connect_timeout_seconds,
            )
            self._session = aiohttp.ClientSession(
                timeout=timeout,
                trust_env=False,
                auto_decompress=True,
                raise_for_status=False,
            )

    async def close(self) -> None:
        """Close owned sockets; safe to call more than once."""

        async with self._lifecycle_lock:
            session, self._session = self._session, None
            if session is not None and not session.closed:
                await session.close()

    async def request(self, request: HttpRequest) -> HttpResponse:
        session = self._session
        if session is None or session.closed:
            raise PaymentProviderUnavailableError("payment_http_transport_not_started")

        self._validate_url(request.url)
        headers = self._validated_headers(request.headers)
        if request.method not in {"GET", "POST"}:
            raise PaymentProviderProtocolError("payment_http_method_invalid")
        if not 0 < request.timeout_seconds <= 120:
            raise PaymentProviderProtocolError("payment_http_timeout_invalid")
        if request.max_response_bytes <= 0:
            raise PaymentProviderProtocolError("payment_http_response_limit_invalid")

        timeout = aiohttp.ClientTimeout(
            total=min(request.timeout_seconds, self._total_timeout_seconds),
            connect=min(
                self._connect_timeout_seconds,
                request.timeout_seconds,
                self._total_timeout_seconds,
            ),
        )
        response_limit = min(request.max_response_bytes, self._max_response_bytes)
        if request.basic_auth is not None:
            try:
                headers["Authorization"] = aiohttp.encode_basic_auth(
                    request.basic_auth.username,
                    request.basic_auth.password.get_secret_value(),
                )
            except ValueError:
                raise PaymentProviderProtocolError("payment_http_auth_invalid") from None

        try:
            async with session.request(
                request.method,
                request.url,
                headers=headers,
                json=dict(request.json_body) if request.json_body is not None else None,
                timeout=timeout,
                allow_redirects=False,
                proxy=None,
                ssl=self._ssl_context,
            ) as response:
                if not 100 <= response.status < 600:
                    raise PaymentProviderProtocolError("payment_http_status_invalid")
                self._check_declared_length(response.headers.get("Content-Length"), response_limit)
                raw = await self._read_bounded(response, response_limit)
                payload = self._decode_json_object(raw)
                return HttpResponse(status_code=response.status, json_body=payload)
        except PaymentProviderError:
            raise
        except (aiohttp.ClientError, TimeoutError, OSError):
            raise PaymentProviderUnavailableError() from None
        except Exception:
            # Third-party exception strings can include request URLs or response data.
            raise PaymentProviderUnavailableError() from None

    def _validate_url(self, url: str) -> None:
        if not url or len(url) > 2048 or "\r" in url or "\n" in url:
            raise PaymentProviderProtocolError("payment_http_url_invalid")
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            raise PaymentProviderProtocolError("payment_http_url_invalid") from None
        host = parsed.hostname.casefold() if parsed.hostname is not None else None
        if (
            parsed.scheme != "https"
            or host not in self._allowed_hosts
            or parsed.username is not None
            or parsed.password is not None
            or port not in (None, 443)
            or bool(parsed.fragment)
        ):
            raise PaymentProviderProtocolError("payment_http_url_not_allowed")

    @staticmethod
    def _validated_headers(headers: Mapping[str, str]) -> dict[str, str]:
        total = 0
        normalized_names: set[str] = set()
        result: dict[str, str] = {}
        try:
            for key, value in headers.items():
                if not isinstance(key, str) or not isinstance(value, str):
                    raise PaymentProviderProtocolError("payment_http_headers_invalid")
                total += len(key) + len(value)
                normalized = key.casefold()
                if (
                    not key
                    or not key.isascii()
                    or _HEADER_NAME.fullmatch(key) is None
                    or "\r" in key
                    or "\n" in key
                    or "\r" in value
                    or "\n" in value
                    or total > 16_384
                    or normalized in normalized_names
                    or normalized in _FORBIDDEN_HEADERS
                ):
                    raise PaymentProviderProtocolError("payment_http_headers_invalid")
                normalized_names.add(normalized)
                result[key] = value
        except PaymentProviderError:
            raise
        except Exception:
            raise PaymentProviderProtocolError("payment_http_headers_invalid") from None
        return result

    @staticmethod
    def _check_declared_length(value: str | None, limit: int) -> None:
        if value is None:
            return
        if len(value) > 10 or not value.isascii() or not value.isdecimal():
            raise PaymentProviderProtocolError("payment_http_content_length_invalid")
        if int(value) > limit:
            raise PaymentProviderProtocolError("payment_http_response_too_large")

    @staticmethod
    async def _read_bounded(response: aiohttp.ClientResponse, limit: int) -> bytes:
        body = bytearray()
        async for chunk in response.content.iter_chunked(_READ_CHUNK_BYTES):
            body.extend(chunk)
            if len(body) > limit:
                raise PaymentProviderProtocolError("payment_http_response_too_large")
        return bytes(body)

    @staticmethod
    def _decode_json_object(raw: bytes) -> dict[str, object]:
        try:
            decoded = raw.decode("utf-8")
            value = json.loads(
                decoded,
                object_pairs_hook=_unique_json_object,
                parse_constant=_reject_json_constant,
            )
        except (UnicodeDecodeError, ValueError, RecursionError):
            raise PaymentProviderProtocolError("payment_http_json_invalid") from None
        if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
            raise PaymentProviderProtocolError("payment_http_json_object_required")
        return value
