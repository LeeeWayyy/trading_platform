"""Redis-backed rate limiting for OAuth2 endpoints.

Multi-layer rate limiting:
- Layer 1: Nginx (configured separately)
- Layer 2: Redis-backed per-user rate limiting (this module)
- Layer 3: Circuit breaker (3 failures → 15-min block)

Limits:
- /callback: 10 requests/minute per IP
- /refresh: 5 requests/minute per session

CRITICAL: Uses Redis for multi-worker Streamlit deployments.
In-memory rate limiters do NOT work across workers.
"""

import logging
from datetime import UTC, datetime

import redis.asyncio

logger = logging.getLogger(__name__)


class RedisRateLimiter:
    """Redis-backed rate limiter with sliding window."""

    def __init__(
        self,
        redis_client: redis.asyncio.Redis,
        max_requests: int,
        window_seconds: int,
        key_prefix: str,
    ):
        """Initialize Redis rate limiter.

        Args:
            redis_client: Redis async client
            max_requests: Maximum requests allowed in window
            window_seconds: Time window in seconds
            key_prefix: Redis key prefix (e.g., "rate_limit:callback:")
        """
        self.redis = redis_client
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.key_prefix = key_prefix

    async def is_allowed(self, identifier: str) -> bool:
        """Check if request is allowed under rate limit.

        Uses Redis sorted set with timestamps for sliding window.

        Args:
            identifier: Rate limit identifier (IP address or session ID)

        Returns:
            True if allowed, False if rate limit exceeded
        """
        key = f"{self.key_prefix}{identifier}"
        now = datetime.now(UTC).timestamp()
        window_start = now - self.window_seconds

        # Remove expired entries (outside sliding window)
        await self.redis.zremrangebyscore(key, 0, window_start)

        # Count requests in current window
        current_count = await self.redis.zcard(key)

        if current_count >= self.max_requests:
            logger.warning(
                "Rate limit exceeded",
                extra={
                    "key": key,
                    "count": current_count,
                    "limit": self.max_requests,
                    "window": self.window_seconds,
                },
            )
            return False

        # Add current request
        await self.redis.zadd(key, {str(now): now})

        # Set expiration to window duration (cleanup)
        await self.redis.expire(key, self.window_seconds)

        return True

    async def reset(self, identifier: str) -> None:
        """Reset rate limit for identifier.

        Args:
            identifier: Rate limit identifier to reset
        """
        key = f"{self.key_prefix}{identifier}"
        await self.redis.delete(key)
