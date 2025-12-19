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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from libs.redis_client import RedisClient


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

    def __init__(self, redis_client: RedisClient) -> None:
        """Initialize the rate limiter.

        Args:
            redis_client: RedisClient instance for rate limit storage
        """
        self.redis = redis_client
        # Namespace by environment to prevent cross-env throttling
        env = os.getenv("ENVIRONMENT", "development")
        self.key = f"{self.KEY_PREFIX}:{env}:reset:global"

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
            # Use dedicated setnx method - truly atomic, no crash risk
            # Returns True if key was set (allowed), False if exists (blocked)
            return self.redis.set_if_not_exists(self.key, "1", ex=window)

        # For limit > 1, use Lua script for atomic INCR + EXPIRE
        # This eliminates the race condition where a crash between INCR and EXPIRE
        # could leave an orphaned key that never expires (permanent lockout)
        new_count = self.redis.eval(_INCR_WITH_EXPIRE_LUA, 1, self.key, str(window))
        return int(new_count) <= limit

    def clear(self) -> None:
        """Clear the rate limit key (rollback on operation failure).

        Call this if the rate-limited operation fails after the token was consumed,
        to prevent blocking legitimate retries.
        """
        self.redis.delete(self.key)
