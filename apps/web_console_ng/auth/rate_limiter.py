from __future__ import annotations

import logging
from collections.abc import Callable
from typing import cast

import redis.asyncio as redis
from redis import exceptions as redis_exceptions

from apps.web_console_ng import config
from apps.web_console_ng.auth.audit import AuthAuditLogger

logger = logging.getLogger(__name__)

# Lua script for CHECK ONLY (no increment) - used before auth attempt
# KEYS[1]: ip_key (auth_rate:ip:...)
# KEYS[2]: lockout_key (auth_lockout:username)
# ARGV[1]: max_ip_attempts
CHECK_ONLY_SCRIPT = """
local ip_key = KEYS[1]
local lockout_key = KEYS[2]
local max_ip_attempts = tonumber(ARGV[1])

-- Check IP rate limit (no increment, just check)
local ip_count = tonumber(redis.call('GET', ip_key) or '0')
if ip_count >= max_ip_attempts then
    return {1, redis.call('TTL', ip_key), 'ip_rate_limit'}
end

-- Check account lockout
local is_locked = redis.call('EXISTS', lockout_key)
if is_locked == 1 then
    return {1, redis.call('TTL', lockout_key), 'account_locked'}
end

return {0, 0, 'allowed'}
"""

# Lua script for RECORD FAILURE (single increment per failed attempt)
# KEYS[1]: ip_key
# KEYS[2]: failure_key (auth_failures:username)
# KEYS[3]: lockout_key (auth_lockout:username)
# ARGV[1]: max_ip_attempts
# ARGV[2]: max_account_attempts
# ARGV[3]: lockout_duration
# ARGV[4]: failure_window
RECORD_FAILURE_SCRIPT = """
local ip_key = KEYS[1]
local failure_key = KEYS[2]
local lockout_key = KEYS[3]
local max_ip_attempts = tonumber(ARGV[1])
local max_account_attempts = tonumber(ARGV[2])
local lockout_duration = tonumber(ARGV[3])
local failure_window = tonumber(ARGV[4])

-- Increment IP rate limit (once per attempt)
local ip_count = redis.call('INCR', ip_key)
if ip_count == 1 then
    redis.call('EXPIRE', ip_key, 60)  -- 1 minute window for IP
end

if ip_count > max_ip_attempts then
    return {0, redis.call('TTL', ip_key), 'ip_rate_limit'}
end

-- Increment failure count for account
local fail_count = redis.call('INCR', failure_key)
if fail_count == 1 then
    redis.call('EXPIRE', failure_key, failure_window)
end

if fail_count >= max_account_attempts then
    -- Lock account AND clear failure count (prevents re-lock after expiry)
    redis.call('SETEX', lockout_key, lockout_duration, '1')
    redis.call('DEL', failure_key)
    return {0, lockout_duration, 'account_locked_now'}
end

return {1, 0, 'failure_recorded', fail_count}
"""


class AuthRateLimiter:
    """Atomic rate limiting for authentication attempts."""

    def __init__(self) -> None:
        self.redis = _redis_from_url(config.REDIS_URL, decode_responses=False)
        self.max_attempts_per_ip = 10  # Per minute
        self.max_attempts_per_account = 5  # Before lockout
        self.lockout_duration = 15 * 60  # 15 minutes
        self.failure_window = 15 * 60  # 15 minute window for failures
        self._check_script_sha: str | None = None
        self._record_script_sha: str | None = None

    async def _load_scripts(self, force: bool = False) -> None:
        """Load Lua scripts (cached SHAs)."""
        if force or self._check_script_sha is None:
            self._check_script_sha = await self.redis.script_load(CHECK_ONLY_SCRIPT)
        if force or self._record_script_sha is None:
            self._record_script_sha = await self.redis.script_load(RECORD_FAILURE_SCRIPT)

    async def check_only(self, client_ip: str, username: str) -> tuple[bool, int, str]:
        """Check rate limits WITHOUT incrementing counters.

        Call this BEFORE attempting authentication.

        Returns:
            (is_blocked, retry_after_seconds, reason)
            Reasons: 'allowed', 'ip_rate_limit', 'account_locked'
        """
        await self._load_scripts()
        assert self._check_script_sha is not None  # Ensured by _load_scripts

        keys = [
            f"auth_rate:ip:{client_ip}",
            f"auth_lockout:{username}",
        ]
        str_args = [str(self.max_attempts_per_ip)]

        # Use evalsha with NOSCRIPT recovery
        try:
            result = await self.redis.evalsha(  # type: ignore[misc]
                self._check_script_sha, len(keys), *keys, *str_args
            )
        except redis_exceptions.NoScriptError:
            await self._load_scripts(force=True)
            result = await self.redis.evalsha(  # type: ignore[misc]
                self._check_script_sha, len(keys), *keys, *str_args
            )
        except redis_exceptions.RedisError as exc:
            if "NOSCRIPT" in str(exc):
                await self._load_scripts(force=True)
                result = await self.redis.evalsha(  # type: ignore[misc]
                    self._check_script_sha, len(keys), *keys, *str_args
                )
            else:
                raise

        is_blocked = bool(result[0])
        retry_after = int(result[1])
        # Decode byte string if necessary (redis-py might return bytes)
        reason = result[2]
        if isinstance(reason, bytes):
            reason = reason.decode("utf-8")

        return is_blocked, retry_after, reason

    async def record_failure(self, client_ip: str, username: str) -> tuple[bool, int, str]:
        """Record a failed authentication attempt (single increment).

        Call this AFTER authentication fails.

        Returns:
            (is_allowed, retry_after_seconds, reason)
            Reasons: 'ip_rate_limit', 'account_locked_now', 'failure_recorded'
        """
        await self._load_scripts()
        assert self._record_script_sha is not None  # Ensured by _load_scripts

        keys = [
            f"auth_rate:ip:{client_ip}",
            f"auth_failures:{username}",
            f"auth_lockout:{username}",
        ]
        str_args = [
            str(self.max_attempts_per_ip),
            str(self.max_attempts_per_account),
            str(self.lockout_duration),
            str(self.failure_window),
        ]

        try:
            result = await self.redis.evalsha(  # type: ignore[misc]
                self._record_script_sha, len(keys), *keys, *str_args
            )
        except redis_exceptions.NoScriptError:
            await self._load_scripts(force=True)
            result = await self.redis.evalsha(  # type: ignore[misc]
                self._record_script_sha, len(keys), *keys, *str_args
            )
        except redis_exceptions.RedisError as exc:
            if "NOSCRIPT" in str(exc):
                await self._load_scripts(force=True)
                result = await self.redis.evalsha(  # type: ignore[misc]
                    self._record_script_sha, len(keys), *keys, *str_args
                )
            else:
                raise

        is_allowed = bool(result[0])
        retry_after = int(result[1])
        reason = result[2]
        if isinstance(reason, bytes):
            reason = reason.decode("utf-8")

        return is_allowed, retry_after, reason

    async def clear_on_success(self, username: str) -> None:
        """Clear failure count and lockout on successful login."""
        await self.redis.delete(
            f"auth_failures:{username}",
            f"auth_lockout:{username}",
        )

    async def unlock_account(self, username: str, admin_user: str) -> bool:
        """Clear lockout state for a user and log audit event."""
        try:
            await self.redis.delete(
                f"auth_failures:{username}",
                f"auth_lockout:{username}",
            )
            audit_logger = AuthAuditLogger.get()
            audit_logger.log_event(
                event_type="account_unlock",
                user_id=username,
                session_id=None,
                client_ip="admin_action",
                user_agent="",
                auth_type=config.AUTH_TYPE,
                outcome="success",
                extra_data={"admin_user": admin_user},
            )
            return True
        except Exception:
            logger.exception("Failed to unlock account for user=%s", username)
            return False


def _redis_from_url(url: str, *, decode_responses: bool) -> redis.Redis:
    from_url = cast(Callable[..., redis.Redis], redis.Redis.from_url)
    return from_url(url, decode_responses=decode_responses)
