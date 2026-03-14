"""Server-side session store backed by Redis with encryption and signing."""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
from collections.abc import Awaitable
from datetime import UTC, datetime
from typing import Any, cast

import redis.asyncio as redis
from cryptography.fernet import Fernet, InvalidToken, MultiFernet

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

# Extended rotation with atomic reverse-index update (force-logout safe).
# KEYS[1] = old session key, KEYS[2] = new session key, KEYS[3] = user index key
# ARGV[1] = new encrypted data, ARGV[2] = TTL in seconds,
# ARGV[3] = old session_id, ARGV[4] = new session_id
_ROTATE_SESSION_WITH_INDEX_LUA = """
local old_data = redis.call("GET", KEYS[1])
if not old_data then
    return nil
end
redis.call("SETEX", KEYS[2], ARGV[2], ARGV[1])
redis.call("DEL", KEYS[1])
redis.call("SREM", KEYS[3], ARGV[3])
redis.call("SADD", KEYS[3], ARGV[4])
local current_ttl = redis.call("TTL", KEYS[3])
local new_ttl = tonumber(ARGV[2])
if current_ttl > new_ttl then
    new_ttl = current_ttl
end
if new_ttl > 0 then
    redis.call("EXPIRE", KEYS[3], new_ttl)
end
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
        self.user_sessions_prefix = "ng_user_sessions:"
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
            session_key = f"{self.session_prefix}{session_id}"

            # [T16.2] Atomic: create session + reverse index in one pipeline
            # to prevent orphaned sessions that force-logout would miss.
            uid = user_data.get("user_id")
            if uid:
                index_key = f"{self.user_sessions_prefix}{uid}"
                # Lazy prune: remove stale session IDs whose keys have expired
                try:
                    existing_ids = await cast(Awaitable[set[Any]], self.redis.smembers(index_key))
                    if existing_ids:
                        sid_list = list(existing_ids)
                        keys = [
                            f"{self.session_prefix}{(s.decode() if isinstance(s, bytes) else str(s))}"
                            for s in sid_list
                        ]
                        # Batch EXISTS via pipeline to avoid N round-trips
                        async with self.redis.pipeline(transaction=False) as pipe:
                            for k in keys:
                                pipe.exists(k)
                            results = await pipe.execute()
                        stale = [s for s, alive in zip(sid_list, results, strict=True) if not alive]
                        if stale:
                            await cast(Awaitable[int], self.redis.srem(index_key, *stale))
                except (redis.RedisError, OSError, TimeoutError):
                    pass  # Best-effort pruning; don't block login
                async with self.redis.pipeline(transaction=True) as pipe:
                    pipe.setex(session_key, self.absolute_timeout, encrypted)
                    pipe.sadd(index_key, session_id)
                    pipe.expire(index_key, self.absolute_timeout)
                    await pipe.execute()
            else:
                await self.redis.setex(session_key, self.absolute_timeout, encrypted)

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

            # [T16.2] Atomic rotation: SETEX new + DELETE old + reverse-index update
            # in one Lua call so force-logout between steps cannot miss the new session.
            uid = _get_user_id(session)
            try:
                encrypted_arg: Any = encrypted
                if uid:
                    index_key = f"{self.user_sessions_prefix}{uid}"
                    result = await self.redis.eval(  # type: ignore[misc]
                        _ROTATE_SESSION_WITH_INDEX_LUA,
                        3,  # number of keys
                        old_key,
                        new_key,
                        index_key,
                        encrypted_arg,
                        str(remaining_ttl),
                        old_session_id,
                        new_session_id,
                    )
                else:
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
                # Fallback for test doubles without EVAL support.
                # Non-atomic: a force-logout between steps could miss the new session.
                if not config.DEBUG:
                    logger.warning(
                        "session_rotation_lua_fallback",
                        extra={"old_session_id": old_session_id},
                    )
                await self.redis.setex(new_key, remaining_ttl, encrypted)
                await self.redis.delete(old_key)
                if uid:
                    index_key = f"{self.user_sessions_prefix}{uid}"
                    await self.redis.srem(index_key, old_session_id)  # type: ignore[misc]
                    await self.redis.sadd(index_key, new_session_id)  # type: ignore[misc]
                    current_ttl_val = await self.redis.ttl(index_key)
                    new_ttl = max(remaining_ttl, current_ttl_val if current_ttl_val > 0 else 0)
                    if new_ttl > 0:
                        await self.redis.expire(index_key, new_ttl)

            cookie_value = self._build_cookie_value(new_session_id)
            if self.audit_logger:
                self.audit_logger.log_event(
                    event_type="session_rotation",
                    user_id=uid,
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
        # [T16.2] Best-effort cleanup of reverse index
        try:
            data = await self.redis.get(f"{self.session_prefix}{session_id}")
            if data:
                decrypted = self.fernet.decrypt(data).decode("utf-8")
                session = json.loads(decrypted)
                uid = _get_user_id(session)
                if uid:
                    await self.redis.srem(f"{self.user_sessions_prefix}{uid}", session_id)  # type: ignore[misc]
        except (redis.RedisError, json.JSONDecodeError, TypeError, AttributeError, InvalidToken) as exc:
            logger.debug("reverse_index_cleanup_failed", extra={"session_id": session_id, "error": str(exc)})
        await self.redis.delete(f"{self.session_prefix}{session_id}")

    async def invalidate_redis_sessions_for_user(self, user_id: str) -> int:
        """Invalidate ALL Redis sessions for a given user_id.

        Uses the reverse index (ng_user_sessions:{user_id} SET) to find
        and delete all session keys atomically via Lua script.
        Returns the number of sessions invalidated.

        Limitations (accepted best-effort trade-offs):
        - Sessions created before the reverse-index feature was deployed are
          not tracked in the index and will not be invalidated.
        - Active NiceGUI WebSocket connections are NOT terminated by this
          call.  WebSocket clients hold in-memory auth state and will
          continue until the next HTTP request triggers middleware
          re-validation.  The role-override middleware enforces the DB
          role on every subsequent request, so stale WebSocket sessions
          cannot escalate privileges.
        """
        index_key = f"{self.user_sessions_prefix}{user_id}"

        # Lua script: atomically read reverse-index, delete sessions + index.
        # Prevents race where a new session is created between read and delete.
        lua = """
        local index_key = KEYS[1]
        local prefix = ARGV[1]
        local sids = redis.call('SMEMBERS', index_key)
        local count = 0
        for _, sid in ipairs(sids) do
            count = count + redis.call('DEL', prefix .. sid)
        end
        redis.call('DEL', index_key)
        return count
        """
        try:
            count = int(
                await self.redis.eval(lua, 1, index_key, self.session_prefix)  # type: ignore[misc]
            )
        except redis.RedisError as lua_err:
            if "unknown command" not in str(lua_err).lower():
                raise
            # Fallback for test doubles without EVAL support.
            # Non-atomic: concurrent session creation may survive invalidation.
            if not config.DEBUG:
                logger.warning(
                    "session_invalidation_lua_fallback",
                    extra={"user_id": user_id},
                )
            session_ids = await self.redis.smembers(index_key)  # type: ignore[misc]
            if not session_ids:
                return 0
            session_keys = []
            for sid_bytes in session_ids:
                sid = sid_bytes.decode("utf-8") if isinstance(sid_bytes, bytes) else str(sid_bytes)
                session_keys.append(f"{self.session_prefix}{sid}")
            async with self.redis.pipeline(transaction=True) as pipe:
                for key in session_keys:
                    pipe.delete(key)
                pipe.delete(index_key)
                results = await pipe.execute()
            count = sum(1 for r in results[:-1] if r)

        logger.info(
            "user_sessions_invalidated",
            extra={"user_id": user_id, "sessions_removed": count},
        )
        return count

    async def update_session_role(self, session_id: str, new_role: str) -> bool:
        """Update the role in an existing Redis session payload.

        Uses WATCH/MULTI optimistic locking to prevent race conditions with
        concurrent validate_session writes (which update last_activity).
        Retries up to 3 times on WatchError before giving up.

        Returns True if the session was found and updated, False otherwise.
        """
        session_key = f"{self.session_prefix}{session_id}"
        max_retries = 3

        for attempt in range(max_retries):
            try:
                # WATCH first, then GET — ensures we detect any concurrent
                # write between our read and the transactional SET.
                async with self.redis.pipeline(transaction=True) as pipe:
                    await pipe.watch(session_key)

                    # Read AFTER watch so any concurrent change triggers WatchError
                    data = await pipe.get(session_key)
                    if not data:
                        await pipe.unwatch()  # type: ignore[no-untyped-call]
                        return False

                    try:
                        decrypted = self.fernet.decrypt(data).decode("utf-8")
                        session = cast(dict[str, Any], json.loads(decrypted))
                    except (InvalidToken, json.JSONDecodeError, TypeError):
                        await pipe.unwatch()  # type: ignore[no-untyped-call]
                        return False

                    user = session.get("user")
                    if not isinstance(user, dict):
                        await pipe.unwatch()  # type: ignore[no-untyped-call]
                        return False

                    if user.get("role") == new_role:
                        await pipe.unwatch()  # type: ignore[no-untyped-call]
                        return True  # Already correct

                    user["role"] = new_role
                    encrypted = self.fernet.encrypt(json.dumps(session).encode())

                    ttl = await pipe.ttl(session_key)
                    if ttl == -2 or ttl == 0:
                        await pipe.unwatch()  # type: ignore[no-untyped-call]
                        return False

                    pipe.multi()  # type: ignore[no-untyped-call]
                    if ttl == -1:
                        pipe.set(session_key, encrypted, xx=True)
                    else:
                        pipe.set(session_key, encrypted, ex=ttl, xx=True)
                    results = await pipe.execute()

                if not results or not results[0]:
                    return False
                return True

            except redis.WatchError:
                if attempt < max_retries - 1:
                    logger.debug(
                        "update_session_role_watch_retry",
                        extra={"session_id": session_id, "attempt": attempt + 1},
                    )
                    continue
                logger.warning(
                    "update_session_role_watch_exhausted",
                    extra={"session_id": session_id},
                )
                return False
        return False  # Exhausted retries without WatchError

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
    "invalidate_redis_sessions_for_user",
]


async def invalidate_redis_sessions_for_user(user_id: str) -> int:
    """Module-level convenience: invalidate all Redis sessions for a user."""
    store = get_session_store()
    return await store.invalidate_redis_sessions_for_user(user_id)
