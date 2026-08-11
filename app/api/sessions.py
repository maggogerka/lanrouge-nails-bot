"""Opaque, short-lived Mini App sessions stored server-side in Redis."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from pydantic import SecretStr

from app.api.telegram_auth import VerifiedWebAppIdentity

_SAFE_NAMESPACE = re.compile(r"^[a-z][a-z0-9_-]{1,47}$")
_OPAQUE_TOKEN = re.compile(r"^[A-Za-z0-9_-]{43}$")
_START_PARAMETER = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MAX_TELEGRAM_ID = (1 << 52) - 1
_SESSION_VERSION = 1


class SessionStoreError(RuntimeError):
    """Safe fail-closed error that never contains Redis data or credentials."""

    def __init__(self) -> None:
        super().__init__("session_store_unavailable")


class RedisSessionClient(Protocol):
    async def set(
        self,
        name: str,
        value: str,
        *,
        ex: int,
        nx: bool,
    ) -> object: ...

    async def get(self, name: str) -> object: ...

    async def delete(self, *names: str) -> object: ...


@dataclass(frozen=True, slots=True)
class OpaqueSession:
    """Trusted server-side identity; this object is never serialized to the client."""

    telegram_user_id: int = field(repr=False)
    issued_at: datetime
    expires_at: datetime
    auth_date: datetime
    start_parameter: str | None = field(default=None, repr=False)


class RedisOpaqueSessionStore:
    """Persist session records under HMAC(token) keys; never persist bearer tokens."""

    def __init__(
        self,
        redis: RedisSessionClient,
        pepper: SecretStr,
        *,
        namespace: str = "telegram_crm",
    ) -> None:
        if _SAFE_NAMESPACE.fullmatch(namespace) is None:
            raise ValueError("namespace must be a safe lowercase identifier")
        raw_pepper = pepper.get_secret_value().encode("utf-8")
        if len(raw_pepper) < 32:
            raise ValueError("session pepper must contain at least 32 UTF-8 bytes")
        self._redis = redis
        self._namespace = namespace
        self._key_secret = hmac.digest(raw_pepper, b"miniapp-session-key-v1", "sha256")

    async def create(self, token: str, session: OpaqueSession, *, ttl_seconds: int) -> bool:
        self._validate_token(token)
        self._validate_session(session)
        if not 60 <= ttl_seconds <= 86_400:
            raise ValueError("session TTL must be between 60 and 86400 seconds")
        value = self._encode(session)
        try:
            result = await self._redis.set(
                self._key(token),
                value,
                ex=ttl_seconds,
                nx=True,
            )
        except Exception:
            raise SessionStoreError() from None
        return result is True

    async def resolve(self, token: str, *, now: datetime | None = None) -> OpaqueSession | None:
        self._validate_token(token)
        key = self._key(token)
        try:
            raw = await self._redis.get(key)
        except Exception:
            raise SessionStoreError() from None
        if raw is None:
            return None
        if isinstance(raw, bytes):
            if len(raw) > 4096:
                raise SessionStoreError()
            try:
                encoded = raw.decode("utf-8")
            except UnicodeDecodeError:
                raise SessionStoreError() from None
        elif isinstance(raw, str) and len(raw.encode("utf-8")) <= 4096:
            encoded = raw
        else:
            raise SessionStoreError()
        session = self._decode(encoded)
        current = _aware_utc(now)
        if session.expires_at <= current:
            await self._delete_key(key)
            return None
        return session

    async def revoke(self, token: str) -> bool:
        self._validate_token(token)
        try:
            deleted = await self._redis.delete(self._key(token))
        except Exception:
            raise SessionStoreError() from None
        return isinstance(deleted, int) and not isinstance(deleted, bool) and deleted > 0

    async def _delete_key(self, key: str) -> None:
        try:
            await self._redis.delete(key)
        except Exception:
            raise SessionStoreError() from None

    def _key(self, token: str) -> str:
        digest = hmac.new(self._key_secret, token.encode("ascii"), hashlib.sha256).hexdigest()
        return f"{self._namespace}:webapp_session:{digest}"

    @staticmethod
    def _validate_token(token: str) -> None:
        if _OPAQUE_TOKEN.fullmatch(token) is None:
            raise ValueError("invalid opaque session token")

    @staticmethod
    def _validate_session(session: OpaqueSession) -> None:
        if not 1 <= session.telegram_user_id <= _MAX_TELEGRAM_ID:
            raise ValueError("invalid Telegram user ID")
        issued_at = _aware_utc(session.issued_at)
        expires_at = _aware_utc(session.expires_at)
        auth_date = _aware_utc(session.auth_date)
        if expires_at <= issued_at or auth_date > issued_at + timedelta(minutes=1):
            raise ValueError("invalid session timestamps")
        if (
            session.start_parameter is not None
            and _START_PARAMETER.fullmatch(session.start_parameter) is None
        ):
            raise ValueError("invalid session start parameter")

    @staticmethod
    def _encode(session: OpaqueSession) -> str:
        return json.dumps(
            {
                "v": _SESSION_VERSION,
                "telegram_user_id": session.telegram_user_id,
                "issued_at": int(session.issued_at.timestamp()),
                "expires_at": int(session.expires_at.timestamp()),
                "auth_date": int(session.auth_date.timestamp()),
                "start_parameter": session.start_parameter,
            },
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )

    @classmethod
    def _decode(cls, value: str) -> OpaqueSession:
        try:
            payload = json.loads(
                value,
                object_pairs_hook=_unique_object,
                parse_constant=_reject_json_constant,
            )
        except (ValueError, RecursionError):
            raise SessionStoreError() from None
        if not isinstance(payload, dict) or set(payload) != {
            "v",
            "telegram_user_id",
            "issued_at",
            "expires_at",
            "auth_date",
            "start_parameter",
        }:
            raise SessionStoreError()
        if payload["v"] != _SESSION_VERSION:
            raise SessionStoreError()
        telegram_id = _integer(payload["telegram_user_id"])
        issued_at = _timestamp(payload["issued_at"])
        expires_at = _timestamp(payload["expires_at"])
        auth_date = _timestamp(payload["auth_date"])
        start_parameter = payload["start_parameter"]
        if start_parameter is not None and not isinstance(start_parameter, str):
            raise SessionStoreError()
        session = OpaqueSession(
            telegram_user_id=telegram_id,
            issued_at=issued_at,
            expires_at=expires_at,
            auth_date=auth_date,
            start_parameter=start_parameter,
        )
        try:
            cls._validate_session(session)
        except ValueError:
            raise SessionStoreError() from None
        return session


class OpaqueSessionIssuer:
    """Exchange a verified one-use Telegram identity for a random bearer session."""

    def __init__(
        self,
        store: RedisOpaqueSessionStore,
        *,
        ttl_seconds: int = 900,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not 60 <= ttl_seconds <= 86_400:
            raise ValueError("session TTL must be between 60 and 86400 seconds")
        self._store = store
        self._ttl_seconds = ttl_seconds
        self._clock = clock or _utc_now

    async def issue_session(
        self,
        identity: VerifiedWebAppIdentity,
        *,
        correlation_id: str,
    ) -> dict[str, object]:
        del correlation_id
        issued_at = _aware_utc(self._clock())
        session = OpaqueSession(
            telegram_user_id=identity.telegram_user_id,
            issued_at=issued_at,
            expires_at=issued_at + timedelta(seconds=self._ttl_seconds),
            auth_date=_aware_utc(identity.auth_date),
            start_parameter=identity.start_parameter,
        )
        for _attempt in range(3):
            token = secrets.token_urlsafe(32)
            if _OPAQUE_TOKEN.fullmatch(token) is None:
                raise RuntimeError("secure token generator returned an invalid token")
            if await self._store.create(token, session, ttl_seconds=self._ttl_seconds):
                return {
                    "session_token": token,
                    "token_type": "Bearer",
                    "expires_in": self._ttl_seconds,
                }
        raise SessionStoreError()


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
    del value
    raise ValueError("non-finite JSON number")


def _integer(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise SessionStoreError()
    return value


def _timestamp(value: object) -> datetime:
    seconds = _integer(value)
    if not 1 <= seconds <= 253_402_300_799:
        raise SessionStoreError()
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        raise SessionStoreError() from None


def _aware_utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return current.astimezone(UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC)
