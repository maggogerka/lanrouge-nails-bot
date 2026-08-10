"""Small transport contracts shared by the dependency-free ASGI layer."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

type AsgiScope = MutableMapping[str, Any]
type AsgiMessage = MutableMapping[str, Any]
type AsgiReceive = Callable[[], Awaitable[AsgiMessage]]
type AsgiSend = Callable[[AsgiMessage], Awaitable[None]]


class SafeHttpError(RuntimeError):
    """Expected boundary rejection carrying no input or infrastructure details."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.safe_message = message
        self.headers = dict(headers or {})
        super().__init__(code)


@dataclass(slots=True)
class HttpRequest:
    """Bounded ASGI request projection; body bytes are never represented or logged."""

    method: str
    path: str
    scheme: str
    headers: Mapping[str, str] = field(repr=False)
    client_host: str = field(repr=False)
    receive: AsgiReceive = field(repr=False)
    query_string: bytes = field(default=b"", repr=False)
    _consumed: bool = field(default=False, init=False, repr=False)

    async def read_body(self, *, max_bytes: int) -> bytes:
        if self._consumed:
            raise SafeHttpError(400, "body_already_consumed", "Invalid request.")
        self._consumed = True
        if not 1 <= max_bytes <= 1_048_576:
            raise ValueError("max_bytes must be between 1 and 1048576")

        raw_length = self.headers.get("content-length")
        if raw_length is not None:
            if len(raw_length) > 10 or not raw_length.isascii() or not raw_length.isdecimal():
                raise SafeHttpError(400, "content_length_invalid", "Invalid request.")
            if int(raw_length) > max_bytes:
                raise SafeHttpError(413, "body_too_large", "Request body is too large.")

        body = bytearray()
        while True:
            message = await self.receive()
            message_type = message.get("type")
            if message_type == "http.disconnect":
                raise SafeHttpError(400, "client_disconnected", "Invalid request.")
            if message_type != "http.request":
                raise SafeHttpError(400, "asgi_message_invalid", "Invalid request.")
            chunk = message.get("body", b"")
            if not isinstance(chunk, bytes):
                raise SafeHttpError(400, "body_chunk_invalid", "Invalid request.")
            body.extend(chunk)
            if len(body) > max_bytes:
                raise SafeHttpError(413, "body_too_large", "Request body is too large.")
            if not bool(message.get("more_body", False)):
                return bytes(body)


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status_code: int
    body: bytes = field(repr=False)
    headers: tuple[tuple[str, str], ...] = ()

    @classmethod
    def json(
        cls,
        status_code: int,
        payload: Mapping[str, object],
        *,
        headers: Mapping[str, str] | None = None,
    ) -> HttpResponse:
        try:
            body = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise RuntimeError("API service returned a non-JSON response") from exc
        response_headers = {
            "content-type": "application/json; charset=utf-8",
            "content-length": str(len(body)),
            **dict(headers or {}),
        }
        return cls(status_code, body, tuple(response_headers.items()))

    @classmethod
    def empty(
        cls,
        status_code: int,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> HttpResponse:
        response_headers = {"content-length": "0", **dict(headers or {})}
        return cls(status_code, b"", tuple(response_headers.items()))


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    """Safe boolean projection; dependency exception messages never cross the boundary."""

    ready: bool
    checks: Mapping[str, bool]

    def __post_init__(self) -> None:
        if not self.checks or len(self.checks) > 32:
            raise ValueError("readiness report must contain 1..32 checks")
        if any(not name or len(name) > 64 for name in self.checks):
            raise ValueError("readiness check names must contain 1..64 characters")


class ReadinessProbe(Protocol):
    async def check(self) -> ReadinessReport: ...


class LifecycleResource(Protocol):
    """A process-owned async resource started and closed by ASGI lifespan."""

    async def start(self) -> None: ...

    async def close(self) -> None: ...


def normalize_headers(raw_headers: object) -> dict[str, str]:
    """Decode bounded ASGI headers and reject ambiguous security-sensitive duplicates."""

    if not isinstance(raw_headers, Sequence) or isinstance(raw_headers, (str, bytes)):
        raise SafeHttpError(400, "headers_invalid", "Invalid request.")
    if len(raw_headers) > 64:
        raise SafeHttpError(431, "headers_too_large", "Request headers are too large.")

    normalized: dict[str, str] = {}
    total_bytes = 0
    for item in raw_headers:
        if not isinstance(item, Sequence) or isinstance(item, (str, bytes)) or len(item) != 2:
            raise SafeHttpError(400, "headers_invalid", "Invalid request.")
        raw_name, raw_value = item
        if not isinstance(raw_name, bytes) or not isinstance(raw_value, bytes):
            raise SafeHttpError(400, "headers_invalid", "Invalid request.")
        total_bytes += len(raw_name) + len(raw_value)
        if total_bytes > 16_384:
            raise SafeHttpError(431, "headers_too_large", "Request headers are too large.")
        try:
            name = raw_name.decode("ascii").lower()
            value = raw_value.decode("latin-1")
        except UnicodeDecodeError as exc:
            raise SafeHttpError(400, "headers_invalid", "Invalid request.") from exc
        if not name or "\r" in value or "\n" in value:
            raise SafeHttpError(400, "headers_invalid", "Invalid request.")
        if name in normalized:
            raise SafeHttpError(400, "duplicate_header", "Invalid request.")
        normalized[name] = value
    return normalized
