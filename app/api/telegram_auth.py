"""Telegram Mini App initData verification and one-time exchange protection."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import parse_qsl

from pydantic import SecretStr

_HASH_PATTERN = re.compile(r"^[0-9A-Fa-f]{64}$")
_AUTH_DATE_PATTERN = re.compile(r"^[0-9]{1,12}$")
_START_PARAMETER_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SAFE_NAMESPACE = re.compile(r"^[a-z][a-z0-9_-]{1,47}$")
_MAX_TELEGRAM_ID = (1 << 52) - 1


class WebAppAuthenticationError(RuntimeError):
    """Safe authentication failure with a stable public code."""

    def __init__(self, code: str = "webapp_authentication_failed") -> None:
        self.code = code
        super().__init__(code)


class ReplayStoreError(RuntimeError):
    """Replay storage is unavailable; authentication must fail closed."""


class ReplayStore(Protocol):
    async def claim(self, fingerprint: str, *, ttl_seconds: int) -> bool: ...


class RedisSetClient(Protocol):
    async def set(
        self,
        name: str,
        value: str,
        *,
        ex: int,
        nx: bool,
    ) -> object: ...


class RedisReplayStore:
    """Atomic SET NX replay guard storing only a keyed digest of initData."""

    def __init__(self, redis: RedisSetClient, *, namespace: str = "lanrouge") -> None:
        if _SAFE_NAMESPACE.fullmatch(namespace) is None:
            raise ValueError("namespace must be a safe lowercase identifier")
        self._redis = redis
        self._namespace = namespace

    async def claim(self, fingerprint: str, *, ttl_seconds: int) -> bool:
        if _HASH_PATTERN.fullmatch(fingerprint) is None:
            raise ValueError("fingerprint must be a SHA-256 hex digest")
        if not 1 <= ttl_seconds <= 86_400:
            raise ValueError("replay TTL must be between 1 and 86400 seconds")
        try:
            result = await self._redis.set(
                f"{self._namespace}:webapp_replay:{fingerprint.lower()}",
                "1",
                ex=ttl_seconds,
                nx=True,
            )
        except Exception:
            raise ReplayStoreError("replay store unavailable") from None
        return result is True


@dataclass(frozen=True, slots=True)
class VerifiedWebAppIdentity:
    """Minimal server-trusted identity; Telegram profile fields stay outside the core."""

    telegram_user_id: int = field(repr=False)
    auth_date: datetime
    session_fingerprint: str = field(repr=False)
    start_parameter: str | None = field(default=None, repr=False)


class TelegramInitDataVerifier:
    """Verify HMAC and freshness before parsing or exposing the Telegram user ID."""

    def __init__(
        self,
        bot_token: SecretStr,
        replay_store: ReplayStore,
        *,
        max_age_seconds: int = 300,
        max_future_skew_seconds: int = 30,
        max_bytes: int = 8192,
        max_fields: int = 32,
    ) -> None:
        token = bot_token.get_secret_value()
        if not token:
            raise ValueError("bot token is required")
        if not 30 <= max_age_seconds <= 3600:
            raise ValueError("max initData age must be between 30 and 3600 seconds")
        if not 0 <= max_future_skew_seconds <= 60:
            raise ValueError("future clock skew must be between 0 and 60 seconds")
        if not 1024 <= max_bytes <= 16_384:
            raise ValueError("initData byte limit must be between 1024 and 16384")
        if not 4 <= max_fields <= 64:
            raise ValueError("initData field limit must be between 4 and 64")

        self._secret_key = hmac.digest(b"WebAppData", token.encode("utf-8"), "sha256")
        self._replay_secret = hmac.digest(self._secret_key, b"replay-fingerprint", "sha256")
        self._replay_store = replay_store
        self._max_age_seconds = max_age_seconds
        self._max_future_skew_seconds = max_future_skew_seconds
        self._max_bytes = max_bytes
        self._max_fields = max_fields

    async def verify_and_claim(
        self,
        raw_init_data: str,
        *,
        now: datetime | None = None,
    ) -> VerifiedWebAppIdentity:
        """Validate a single-use initData exchange and return only trusted fields."""

        fields, received_hash = self._verify_signature(raw_init_data)
        current = now or datetime.now(UTC)
        if current.tzinfo is None or current.utcoffset() is None:
            raise ValueError("now must be timezone-aware")
        current_timestamp = int(current.timestamp())
        auth_timestamp = self._auth_timestamp(fields)
        age = current_timestamp - auth_timestamp
        if age > self._max_age_seconds:
            raise WebAppAuthenticationError("webapp_init_data_expired")
        if age < -self._max_future_skew_seconds:
            raise WebAppAuthenticationError("webapp_auth_date_in_future")

        user_id = self._telegram_user_id(fields)
        fingerprint = hmac.new(
            self._replay_secret,
            received_hash.lower().encode("ascii"),
            hashlib.sha256,
        ).hexdigest()
        ttl_seconds = max(
            1,
            min(self._max_age_seconds, auth_timestamp + self._max_age_seconds - current_timestamp),
        )
        try:
            claimed = await self._replay_store.claim(
                fingerprint,
                ttl_seconds=ttl_seconds,
            )
        except ReplayStoreError:
            raise
        except Exception:
            raise ReplayStoreError("replay store unavailable") from None
        if not claimed:
            raise WebAppAuthenticationError("webapp_init_data_replayed")

        raw_start_parameter = fields.get("start_param")
        start_parameter = (
            raw_start_parameter
            if raw_start_parameter is not None
            and _START_PARAMETER_PATTERN.fullmatch(raw_start_parameter) is not None
            else None
        )
        return VerifiedWebAppIdentity(
            telegram_user_id=user_id,
            auth_date=datetime.fromtimestamp(auth_timestamp, tz=UTC),
            session_fingerprint=fingerprint,
            start_parameter=start_parameter,
        )

    def _verify_signature(self, raw_init_data: str) -> tuple[dict[str, str], str]:
        if not raw_init_data or len(raw_init_data.encode("utf-8")) > self._max_bytes:
            raise WebAppAuthenticationError()
        try:
            pairs = parse_qsl(
                raw_init_data,
                keep_blank_values=True,
                strict_parsing=True,
                max_num_fields=self._max_fields,
                encoding="utf-8",
                errors="strict",
            )
        except (UnicodeDecodeError, ValueError):
            raise WebAppAuthenticationError() from None
        fields: dict[str, str] = {}
        for key, value in pairs:
            if not key or key in fields:
                raise WebAppAuthenticationError()
            fields[key] = value
        received_hash = fields.pop("hash", None)
        if received_hash is None or _HASH_PATTERN.fullmatch(received_hash) is None:
            raise WebAppAuthenticationError()
        data_check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
        expected_hash = hmac.new(
            self._secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected_hash, received_hash.lower()):
            raise WebAppAuthenticationError()
        return fields, received_hash

    @staticmethod
    def _auth_timestamp(fields: dict[str, str]) -> int:
        raw_auth_date = fields.get("auth_date")
        if raw_auth_date is None or _AUTH_DATE_PATTERN.fullmatch(raw_auth_date) is None:
            raise WebAppAuthenticationError()
        auth_timestamp = int(raw_auth_date)
        if auth_timestamp <= 0:
            raise WebAppAuthenticationError()
        return auth_timestamp

    @staticmethod
    def _telegram_user_id(fields: dict[str, str]) -> int:
        raw_user = fields.get("user")
        if raw_user is None or len(raw_user.encode("utf-8")) > 4096:
            raise WebAppAuthenticationError()
        try:
            user = json.loads(raw_user)
        except (json.JSONDecodeError, RecursionError):
            raise WebAppAuthenticationError() from None
        if not isinstance(user, dict) or any(not isinstance(key, str) for key in user):
            raise WebAppAuthenticationError()
        raw_user_id = user.get("id")
        if (
            not isinstance(raw_user_id, int)
            or isinstance(raw_user_id, bool)
            or not 1 <= raw_user_id <= _MAX_TELEGRAM_ID
        ):
            raise WebAppAuthenticationError()
        return raw_user_id
