"""Atomic rate limiter for circuit breaker reset operations.

This module provides global rate limiting for circuit breaker resets to prevent
accidental spam or abuse. Uses Redis SET NX EX for true atomicity (prevents
infinite lockout on crash between INCR and EXPIRE).

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
        For limit>1: Uses INCR + EXPIRE (documented behavior).

        Args:
            limit: Maximum resets allowed in window (default: 1)
            window: Window size in seconds (default: 60)

        Returns:
            True if reset allowed, False if rate limited
        """
        if limit == 1:
            # SET NX EX is truly atomic - no crash risk between commands
            # Returns True if key was set (allowed), False if exists (blocked)
            return bool(self.redis.set(self.key, "1", nx=True, ex=window))

        # For limit > 1, use INCR + conditional EXPIRE
        # Note: Crash between INCR and EXPIRE can cause orphaned key,
        # but for CB reset with limit=1 this path isn't used.
        new_count = self.redis.incr(self.key)
        if new_count == 1:
            self.redis.expire(self.key, window)
        return new_count <= limit

    def clear(self) -> None:
        """Clear the rate limit key (rollback on operation failure).

        Call this if the rate-limited operation fails after the token was consumed,
        to prevent blocking legitimate retries.
        """
        self.redis.delete(self.key)
