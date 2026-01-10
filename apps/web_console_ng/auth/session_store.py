"""Server-side session store backed by Redis with encryption and signing."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
from datetime import UTC, datetime
from typing import Any, cast

import redis.asyncio as redis
from cryptography.fernet import Fernet, MultiFernet

from apps.web_console_ng import config
from apps.web_console_ng.auth.audit import AuthAuditLogger
from apps.web_console_ng.core.redis_ha import get_redis_store

logger = logging.getLogger(__name__)

_RATE_LIMIT_LUA = """
local current = redis.call("INCR", KEYS[1])
if current == 1 then
  redis.call("EXPIRE", KEYS[1], ARGV[1])
end
return current
"""

# Atomic session rotation: get old session, create new, delete old in one transaction
# KEYS[1] = old session key, KEYS[2] = new session key
# ARGV[1] = new encrypted data, ARGV[2] = TTL in seconds
# Returns: old encrypted data if exists, nil otherwise
_ROTATE_SESSION_LUA = """
local old_data = redis.call("GET", KEYS[1])
if not old_data then
    return nil
end
redis.call("SETEX", KEYS[2], ARGV[2], ARGV[1])
redis.call("DEL", KEYS[1])
return old_data
"""


class SessionCreationError(Exception):
    """Raised when session creation fails (Redis unavailable, etc.)."""


class SessionValidationError(Exception):
    """Raised when session validation fails due to infrastructure errors.

    This is distinct from invalid sessions - callers should respond with 503
    (Service Unavailable) rather than 401 (Unauthorized) when this is raised.
    """


class RateLimitExceeded(Exception):
    """Raised when a rate limit is exceeded."""


class ServerSessionStore:
    """Server-side session store with async Redis backend.

    IMPORTANT: Always use get_session_store() to instantiate, which provides
    a redis_client from get_redis_store() for HA/Sentinel support.
    """

    def __init__(
        self,
        redis_url: str,
        encryption_keys: list[bytes],
        signing_keys: dict[str, bytes],
        current_signing_key_id: str,
        redis_client: redis.Redis,
        audit_logger: AuthAuditLogger | None = None,
    ) -> None:
        # SECURITY: redis_client must be provided via get_redis_store() for HA support
        # Do not fall back to direct Redis.from_url() as it bypasses Sentinel/TLS config
        self.redis: redis.Redis = redis_client
        self.fernet = MultiFernet([Fernet(_normalize_fernet_key(k)) for k in encryption_keys])
        self.signing_keys = signing_keys
        self.current_signing_key_id = current_signing_key_id
        self.session_prefix = "ng_session:"
        self.rate_limit_prefix = "ng_rate:"
        self.idle_timeout = config.SESSION_IDLE_TIMEOUT_MINUTES * 60
        self.absolute_timeout = config.SESSION_ABSOLUTE_TIMEOUT_HOURS * 3600
        self.audit_logger = audit_logger

    async def create_session(
        self,
        user_data: dict[str, Any],
        device_info: dict[str, Any],
        client_ip: str,
    ) -> tuple[str, str]:
        """Create session and return (cookie_value, csrf_token)."""
        try:
            if not await self._check_rate_limit(client_ip, "create", 10):
                if self.audit_logger:
                    self.audit_logger.log_event(
                        event_type="rate_limit_exceeded",
                        user_id=user_data.get("user_id"),
                        session_id=None,
                        client_ip=client_ip,
                        user_agent=str(device_info.get("user_agent", "")),
                        auth_type=config.AUTH_TYPE,
                        outcome="failure",
                        failure_reason="create_rate_limit",
                    )
                raise RateLimitExceeded("Session creation rate limit exceeded")

            session_id = secrets.token_urlsafe(32)
            csrf_token = secrets.token_urlsafe(32)
            now = datetime.now(UTC)

            session_data = {
                "session_id": session_id,
                "user": user_data,
                "csrf_token": csrf_token,
                "created_at": now.isoformat(),
                "issued_at": now.isoformat(),
                "last_activity": now.isoformat(),
                "device": self._build_device_info(client_ip, device_info),
            }

            encrypted = self.fernet.encrypt(json.dumps(session_data).encode())
            await self.redis.setex(
                f"{self.session_prefix}{session_id}",
                self.absolute_timeout,
                encrypted,
            )

            cookie_value = self._build_cookie_value(session_id)
            if self.audit_logger:
                self.audit_logger.log_event(
                    event_type="login_success",
                    user_id=user_data.get("user_id"),
                    session_id=session_id,
                    client_ip=client_ip,
                    user_agent=str(device_info.get("user_agent", "")),
                    auth_type=config.AUTH_TYPE,
                    outcome="success",
                )
            return cookie_value, csrf_token
        except redis.RedisError as exc:
            logger.error("Redis error during session creation: %s", exc)
            raise SessionCreationError("Session creation failed - storage unavailable") from exc

    async def validate_session(
        self,
        cookie_value: str,
        client_ip: str,
        user_agent: str | None = None,
    ) -> dict[str, Any] | None:
        """Validate session and return data if valid.

        Cookie value format: {session_id}.{key_id}:{signature}
        """
        user_agent = user_agent or ""
        try:
            if not await self._check_rate_limit(client_ip, "validate", 100):
                if self.audit_logger:
                    self.audit_logger.log_event(
                        event_type="rate_limit_exceeded",
                        user_id=None,
                        session_id=None,
                        client_ip=client_ip,
                        user_agent=user_agent,
                        auth_type=config.AUTH_TYPE,
                        outcome="failure",
                        failure_reason="validate_rate_limit",
                    )
                return None

            session_id, key_sig = self._parse_cookie(cookie_value)
            if session_id is None or key_sig is None:
                self._audit_failure(
                    "session_validation_failure",
                    None,
                    session_id,
                    client_ip,
                    user_agent,
                    "malformed_cookie",
                )
                return None

            if not self._verify_signature(session_id, key_sig):
                self._audit_failure(
                    "session_validation_failure",
                    None,
                    session_id,
                    client_ip,
                    user_agent,
                    "invalid_signature",
                )
                return None

            data = await self.redis.get(f"{self.session_prefix}{session_id}")
            if not data:
                return None

            try:
                decrypted = self.fernet.decrypt(data).decode("utf-8")
            except (TypeError, AttributeError) as exc:
                # Cryptographic errors: corrupt data, wrong key, or invalid input
                logger.warning(
                    "Session decryption failed",
                    extra={
                        "session_id": session_id,
                        "client_ip": client_ip,
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    },
                )
                await self.invalidate_session(session_id)
                self._audit_failure(
                    "session_validation_failure",
                    None,
                    session_id,
                    client_ip,
                    user_agent,
                    f"decrypt_error:{type(exc).__name__}",
                )
                return None

            try:
                session = cast(dict[str, Any], json.loads(decrypted))
            except json.JSONDecodeError:
                await self.invalidate_session(session_id)
                self._audit_failure(
                    "session_validation_failure",
                    None,
                    session_id,
                    client_ip,
                    user_agent,
                    "json_decode_error",
                )
                return None
            if not isinstance(session, dict):
                await self.invalidate_session(session_id)
                self._audit_failure(
                    "session_validation_failure",
                    None,
                    session_id,
                    client_ip,
                    user_agent,
                    "session_payload_invalid",
                )
                return None

            # Validate required fields - missing/corrupt fields invalidate session
            try:
                now = datetime.now(UTC)
                created_at = datetime.fromisoformat(session["created_at"]).replace(tzinfo=UTC)
                last_activity = datetime.fromisoformat(session["last_activity"]).replace(tzinfo=UTC)
            except (KeyError, ValueError, TypeError) as exc:
                await self.invalidate_session(session_id)
                self._audit_failure(
                    "session_validation_failure",
                    _get_user_id(session),
                    session_id,
                    client_ip,
                    user_agent,
                    f"corrupt_session_payload:{type(exc).__name__}",
                )
                return None

            age_seconds = (now - created_at).total_seconds()
            if age_seconds > self.absolute_timeout:
                await self.invalidate_session(session_id)
                self._audit_failure(
                    "session_validation_failure",
                    _get_user_id(session),
                    session_id,
                    client_ip,
                    user_agent,
                    "absolute_timeout",
                )
                return None

            if (now - last_activity).total_seconds() > self.idle_timeout:
                await self.invalidate_session(session_id)
                self._audit_failure(
                    "session_validation_failure",
                    _get_user_id(session),
                    session_id,
                    client_ip,
                    user_agent,
                    "idle_timeout",
                )
                return None

            if config.DEVICE_BINDING_ENABLED:
                expected = session.get("device") or {}
                current = self._build_device_info(client_ip, {"user_agent": user_agent})
                if expected.get("ip_subnet") != current.get("ip_subnet") or expected.get(
                    "ua_hash"
                ) != current.get("ua_hash"):
                    await self.invalidate_session(session_id)
                    self._audit_failure(
                        "device_mismatch",
                        _get_user_id(session),
                        session_id,
                        client_ip,
                        user_agent,
                        "device_binding_failed",
                    )
                    return None

            remaining_ttl = int(self.absolute_timeout - age_seconds)
            if remaining_ttl <= 0:
                await self.invalidate_session(session_id)
                return None

            session["last_activity"] = now.isoformat()
            encrypted = self.fernet.encrypt(json.dumps(session).encode())
            await self.redis.setex(
                f"{self.session_prefix}{session_id}",
                remaining_ttl,
                encrypted,
            )

            return session
        except redis.RedisError as exc:
            # Infrastructure error - callers should respond with 503, not 401
            logger.error("Redis error during session validation: %s", exc)
            raise SessionValidationError("Session validation failed - storage unavailable") from exc

    async def rotate_session(
        self,
        old_session_id: str,
        user_updates: dict[str, Any] | None = None,
    ) -> tuple[str, str] | None:
        """Rotate session ID and CSRF token for fixation protection.

        Args:
            old_session_id: The session ID to rotate.
            user_updates: Optional dict of fields to merge into session["user"].
                         Useful for clearing mfa_pending after MFA verification.

        Returns (cookie_value, csrf_token) or None on failure.

        Note: Uses Lua script for atomic SETEX+DELETE to prevent race conditions
        where both old and new sessions could briefly be valid.
        """
        try:
            old_key = f"{self.session_prefix}{old_session_id}"
            data = await self.redis.get(old_key)
            if not data:
                return None

            decrypted = self.fernet.decrypt(data).decode("utf-8")
            session = cast(dict[str, Any], json.loads(decrypted))
            created_at = datetime.fromisoformat(session["created_at"]).replace(tzinfo=UTC)
            now = datetime.now(UTC)
            remaining_ttl = int(self.absolute_timeout - (now - created_at).total_seconds())

            if remaining_ttl <= 0:
                await self.redis.delete(old_key)
                return None

            new_session_id = secrets.token_urlsafe(32)
            new_csrf = secrets.token_urlsafe(32)
            session["session_id"] = new_session_id
            session["csrf_token"] = new_csrf
            session["issued_at"] = now.isoformat()
            session["last_activity"] = now.isoformat()

            # Apply user updates if provided (e.g., clear mfa_pending)
            if user_updates and isinstance(session.get("user"), dict):
                session["user"].update(user_updates)

            encrypted = self.fernet.encrypt(json.dumps(session).encode())
            new_key = f"{self.session_prefix}{new_session_id}"

            # Atomic rotation: SETEX new session + DELETE old session in one Lua call
            # This prevents race conditions where both sessions could be valid briefly
            try:
                # Cast encrypted bytes to Any for eval() - Redis handles binary data fine
                encrypted_arg: Any = encrypted
                result = await self.redis.eval(  # type: ignore[misc]
                    _ROTATE_SESSION_LUA,
                    2,  # number of keys
                    old_key,
                    new_key,
                    encrypted_arg,
                    str(remaining_ttl),
                )
                if result is None:
                    # Old session was deleted between GET and rotation - abort
                    return None
            except redis.RedisError as lua_err:
                if "unknown command" not in str(lua_err).lower():
                    raise
                # Fallback for test doubles without EVAL support
                await self.redis.setex(new_key, remaining_ttl, encrypted)
                await self.redis.delete(old_key)

            cookie_value = self._build_cookie_value(new_session_id)
            if self.audit_logger:
                self.audit_logger.log_event(
                    event_type="session_rotation",
                    user_id=_get_user_id(session),
                    session_id=new_session_id,
                    client_ip=session.get("device", {}).get("ip_subnet", ""),
                    user_agent="",
                    auth_type=config.AUTH_TYPE,
                    outcome="success",
                )
            return cookie_value, new_csrf
        except redis.RedisError as exc:
            # Redis errors during rotation - log with context
            logger.error(
                "Session rotation failed - Redis error",
                extra={
                    "old_session_id": old_session_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            return None
        except (TypeError, AttributeError, ValueError, KeyError) as exc:
            # Data corruption or invalid session structure
            logger.warning(
                "Session rotation failed - invalid session data",
                extra={
                    "old_session_id": old_session_id,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            )
            return None

    async def invalidate_session(self, session_id: str) -> None:
        await self.redis.delete(f"{self.session_prefix}{session_id}")

    async def _check_rate_limit(self, client_ip: str, action: str, limit: int) -> bool:
        key = f"{self.rate_limit_prefix}{action}:{client_ip}"
        ttl_seconds = 60
        try:
            count_raw = await self.redis.eval(  # type: ignore[misc]
                _RATE_LIMIT_LUA, 1, key, str(ttl_seconds)
            )
        except redis.RedisError as exc:
            if "unknown command" not in str(exc).lower():
                raise
            # Fallback for test doubles (e.g., fakeredis) that don't implement EVAL.
            # This fallback is NOT atomic (incr+expire race condition).
            # Log warning in production to detect if this path is hit unexpectedly.
            if not config.DEBUG:
                logger.warning(
                    "Rate limit fallback triggered - EVAL not supported. "
                    "This should only happen in tests, not production."
                )
            count_raw = await self.redis.incr(key)
            if int(cast(int, count_raw)) == 1:
                await self.redis.expire(key, ttl_seconds)
        count = int(cast(int, count_raw))
        return count <= limit

    def _build_cookie_value(self, session_id: str) -> str:
        key_sig = self._sign_with_key_id(session_id)
        return f"{session_id}.{key_sig}"

    def _sign_with_key_id(self, data: str) -> str:
        key = self.signing_keys[self.current_signing_key_id]
        sig = hmac.new(key, data.encode(), hashlib.sha256).hexdigest()
        return f"{self.current_signing_key_id}:{sig}"

    def _parse_cookie(self, cookie_value: str) -> tuple[str | None, str | None]:
        if "." not in cookie_value:
            return None, None
        parts = cookie_value.rsplit(".", 1)
        if len(parts) != 2:
            return None, None
        session_id, key_sig = parts
        if ":" not in key_sig:
            return None, None
        return session_id, key_sig

    def _verify_signature(self, data: str, signature: str) -> bool:
        if ":" not in signature:
            return False
        key_id, sig = signature.split(":", 1)
        if key_id not in self.signing_keys:
            logger.warning(
                "Cookie signature verification failed: key_id='%s' not found in signing_keys",
                key_id,
            )
            return False
        key = self.signing_keys[key_id]
        expected = hmac.new(key, data.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(sig, expected):
            logger.warning("Cookie signature verification failed: signature mismatch")
            return False
        return True

    def verify_cookie(self, cookie_value: str) -> str | None:
        """Return session_id if cookie signature is valid, otherwise None."""
        session_id, key_sig = self._parse_cookie(cookie_value)
        if session_id is None or key_sig is None:
            return None
        if not self._verify_signature(session_id, key_sig):
            return None
        return session_id

    def _build_device_info(self, client_ip: str, device_info: dict[str, Any]) -> dict[str, str]:
        user_agent = str(device_info.get("user_agent", ""))
        ua_hash = device_info.get("ua_hash") or hashlib.sha256(user_agent.encode()).hexdigest()
        ip_subnet = _ip_subnet(client_ip, config.DEVICE_BINDING_SUBNET_MASK)
        return {"ip_subnet": ip_subnet, "ua_hash": str(ua_hash)}

    def _audit_failure(
        self,
        event_type: str,
        user_id: str | None,
        session_id: str | None,
        client_ip: str,
        user_agent: str,
        failure_reason: str,
    ) -> None:
        if not self.audit_logger:
            return
        self.audit_logger.log_event(
            event_type=event_type,
            user_id=user_id,
            session_id=session_id,
            client_ip=client_ip,
            user_agent=user_agent,
            auth_type=config.AUTH_TYPE,
            outcome="failure",
            failure_reason=failure_reason,
        )


_store: ServerSessionStore | None = None


def get_session_store(audit_logger: AuthAuditLogger | None = None) -> ServerSessionStore:
    global _store
    if _store is None:
        signing_keys = config.get_signing_keys()
        current_key_id = config.HMAC_CURRENT_KEY_ID or next(iter(signing_keys))
        resolved_logger = audit_logger or AuthAuditLogger.get(
            db_enabled=config.AUDIT_LOG_DB_ENABLED
        )
        redis_store = get_redis_store()
        redis_client = redis_store.get_master_client(decode_responses=False)
        _store = ServerSessionStore(
            redis_url=config.REDIS_URL,
            encryption_keys=config.get_encryption_keys(),
            signing_keys=signing_keys,
            current_signing_key_id=current_key_id,
            redis_client=redis_client,
            audit_logger=resolved_logger,
        )
    elif audit_logger is not None and _store.audit_logger is None:
        _store.audit_logger = audit_logger
    return _store


def _normalize_fernet_key(key: bytes) -> bytes:
    if len(key) == 44:
        return key
    if len(key) == 32:
        return base64.urlsafe_b64encode(key)
    return base64.urlsafe_b64encode(key)


def _ip_subnet(client_ip: str, mask_bits: int) -> str:
    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError as exc:
        # Invalid IP format - return original for best-effort tracking
        logger.debug(
            "Invalid IP address format for subnet calculation",
            extra={"client_ip": client_ip, "error": str(exc)},
        )
        return client_ip
    max_bits = 32 if addr.version == 4 else 128
    mask = max(0, min(mask_bits, max_bits))
    network = ipaddress.ip_network(f"{addr}/{mask}", strict=False)
    return str(network)


def _get_user_id(session: dict[str, Any]) -> str | None:
    user = session.get("user") or {}
    return user.get("user_id") if isinstance(user, dict) else None


def extract_session_id(signed_cookie: str) -> str:
    """Extract session ID from signed cookie.

    Cookie format: {session_id}.{key_id}:{signature}
    Uses rsplit to correctly handle session IDs that may contain dots.
    """
    if not signed_cookie:
        raise ValueError("Empty cookie")
    # Split from right to isolate key_id:signature part first
    parts = signed_cookie.rsplit(".", 1)
    if len(parts) != 2:
        raise ValueError("Invalid cookie format: missing signature")
    session_id = parts[0]
    if not session_id:
        raise ValueError("Invalid cookie format: empty session ID")
    return session_id


__all__ = [
    "ServerSessionStore",
    "SessionCreationError",
    "SessionValidationError",
    "RateLimitExceeded",
    "get_session_store",
    "extract_session_id",
]
