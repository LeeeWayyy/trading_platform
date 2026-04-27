"""Atomic rate limiter for circuit breaker reset operations.

This module provides global rate limiting for circuit breaker resets to prevent
accidental spam or abuse. Uses atomic Redis operations to prevent race conditions:
- For limit=1: SET NX EX (truly atomic)
- For limit>1: Lua script (INCR + EXPIRE in single atomic operation)

The rate limit is GLOBAL (not per-user) because circuit breaker resets affect
the entire trading system and should be deliberate, rare operations.

The key is namespaced by ENVIRONMENT to prevent cross-env throttling
(e.g., paper env reset shouldn't block live env reset).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Protocol, cast, runtime_checkable

import redis

from libs.core.redis_client import RedisClient


@runtime_checkable
class RedisRateLimitClient(Protocol):
    """Redis operations required for circuit-breaker reset rate limiting."""

    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> int | str:
        """Run a Redis Lua script."""
        ...

    def delete(self, *keys: str) -> object:
        """Delete Redis keys."""
        ...


@runtime_checkable
class RedisWrapperRateLimitClient(RedisRateLimitClient, Protocol):
    """Project RedisClient wrapper with SET NX helper."""

    def set_if_not_exists(self, key: str, value: str, ex: int | None = None) -> bool:
        """Set a key only when it does not already exist."""
        ...


@runtime_checkable
class RedisPyRateLimitClient(RedisRateLimitClient, Protocol):
    """redis-py client surface used by the NiceGUI sync dependency."""

    def set(
        self,
        name: str,
        value: str,
        ex: int | None = None,
        nx: bool = False,
    ) -> object:
        """Set a key with optional expiration and NX semantics."""
        ...


# Lua script for atomic INCR + EXPIRE (prevents race condition/orphaned keys)
# Returns: new count after increment
# Also handles edge case where key exists without TTL (e.g., leftover from crash)
_INCR_WITH_EXPIRE_LUA = """
local key = KEYS[1]
local window = tonumber(ARGV[1])
local count = redis.call('INCR', key)
if count == 1 or redis.call('TTL', key) == -1 then
    -- Set TTL on first increment OR if key has no expiry (orphaned key)
    redis.call('EXPIRE', key, window)
end
return count
"""


class CBRateLimiter:
    """Atomic rate limiter for CB reset operations.

    Uses a GLOBAL key (not per-user) to prevent ANY user from
    resetting the circuit breaker more than once per minute.

    Key is namespaced by ENVIRONMENT to prevent cross-env throttling.

    Example:
        >>> limiter = CBRateLimiter(redis_client)
        >>> if limiter.check_global(limit=1, window=60):
        ...     # OK to reset
        ...     perform_reset()
        ... else:
        ...     # Rate limited
        ...     raise RateLimitExceeded("Max 1 reset per minute")
    """

    KEY_PREFIX = "cb_ratelimit"

    def __init__(self, redis_client: RedisClient | redis.Redis) -> None:
        """Initialize the rate limiter.

        Args:
            redis_client: RedisClient instance for rate limit storage
        """
        self.redis = redis_client
        self._set_once = self._resolve_set_once(redis_client)
        # Namespace by environment to prevent cross-env throttling
        env = os.getenv("ENVIRONMENT", "development")
        self.key = f"{self.KEY_PREFIX}:{env}:reset:global"

    def _resolve_set_once(
        self, redis_client: RedisClient | redis.Redis
    ) -> Callable[[str, str, int], bool]:
        """Resolve the concrete SET NX EX operation once during construction."""
        if isinstance(redis_client, RedisWrapperRateLimitClient):
            wrapper_client = cast(RedisWrapperRateLimitClient, redis_client)
            return lambda key, value, ex: wrapper_client.set_if_not_exists(
                key, value, ex=ex
            )

        if isinstance(redis_client, RedisPyRateLimitClient):
            redis_py_client = cast(RedisPyRateLimitClient, redis_client)
            return lambda key, value, ex: bool(
                redis_py_client.set(key, value, ex=ex, nx=True)
            )

        msg = f"Unsupported Redis rate-limit client: {type(redis_client).__name__}"
        raise TypeError(msg)

    def check_global(self, limit: int = 1, window: int = 60) -> bool:
        """Check if global reset is allowed (atomic).

        For limit=1: Uses SET NX EX for true atomicity (no infinite lockout risk).
        For limit>1: Uses Lua script for atomic INCR + EXPIRE (no race condition).

        Args:
            limit: Maximum resets allowed in window (default: 1)
            window: Window size in seconds (default: 60)

        Returns:
            True if reset allowed, False if rate limited
        """
        if limit == 1:
            return self._set_once(self.key, "1", window)

        # For limit > 1, use Lua script for atomic INCR + EXPIRE
        # This eliminates the race condition where a crash between INCR and EXPIRE
        # could leave an orphaned key that never expires (permanent lockout)
        new_count = cast(
            int | str, self.redis.eval(_INCR_WITH_EXPIRE_LUA, 1, self.key, str(window))
        )
        return int(new_count) <= limit

    def clear(self) -> None:
        """Clear the rate limit key (rollback on operation failure).

        Call this if the rate-limited operation fails after the token was consumed,
        to prevent blocking legitimate retries.
        """
        self.redis.delete(self.key)
