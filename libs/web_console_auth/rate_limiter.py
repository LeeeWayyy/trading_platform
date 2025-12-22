"""Redis sliding-window rate limiter shared across services."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Awaitable
from typing import Any, cast

import redis.asyncio as redis
from prometheus_client import Counter

logger = logging.getLogger(__name__)

rate_limit_checks_total = Counter(
    "rate_limit_checks_total", "Total rate limit checks", ["action", "result"]
)
rate_limit_redis_errors_total = Counter(
    "rate_limit_redis_errors_total", "Redis errors during rate limit checks", ["action"]
)

ALERT_RATE_LIMITS: dict[str, Any] = {
    "channel": {"email": 100, "slack": 50, "sms": 10},  # per minute
    "recipient": {"email": 5, "slack": 10, "sms": 3},  # per hour
    "global": 500,  # per minute
}

ALERT_RATE_LIMIT_TTL: dict[str, int] = {
    "channel": 60,  # 60 seconds for per-minute limits
    "recipient": 3600,  # 1 hour for per-hour limits
    "global": 60,  # 60 seconds for global burst
}


class RateLimiter:
    """Sliding-window rate limiter using Redis sorted sets."""

    def __init__(
        self,
        redis_client: redis.Redis,
        fallback_mode: str = "deny",
        max_requests: int | None = None,
        window_seconds: int | None = None,
        key_prefix: str = "rl:",
    ) -> None:
        self.redis = redis_client
        self.fallback_mode = fallback_mode
        self.default_max_requests = max_requests
        self.default_window_seconds = window_seconds
        self.key_prefix = key_prefix

    _RATE_LIMIT_SCRIPT = """
    local key = KEYS[1]
    local now = tonumber(ARGV[1])
    local window = tonumber(ARGV[2])
    local max_requests = tonumber(ARGV[3])
    local member = ARGV[4]

    redis.call('ZADD', key, now, member)
    redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
    local count = redis.call('ZCARD', key)
    redis.call('EXPIRE', key, window)

    return count
    """

    async def check_rate_limit(
        self,
        user_id: str,
        action: str,
        max_requests: int,
        window_seconds: int,
        *,
        fallback_mode: str | None = None,
    ) -> tuple[bool, int]:
        now = int(time.time())
        key = f"{self.key_prefix}{action}:{user_id}"
        try:
            member = f"{user_id}:{time.time_ns()}"
            eval_result = self.redis.eval(
                self._RATE_LIMIT_SCRIPT,
                1,
                key,
                str(now),
                str(window_seconds),
                str(max_requests),
                member,
            )
            if hasattr(eval_result, "__await__"):
                result = await eval_result
            else:
                result = eval_result
            count = int(result)
            allowed = count <= max_requests
            rate_limit_checks_total.labels(
                action=action, result="allowed" if allowed else "blocked"
            ).inc()
            return allowed, max(0, max_requests - count)
        except Exception as exc:  # pragma: no cover - defensive path
            rate_limit_redis_errors_total.labels(action=action).inc()
            logger.warning("rate_limit_fallback", extra={"action": action, "error": str(exc)})
            mode = fallback_mode or self.fallback_mode or "deny"
            if mode == "deny":
                rate_limit_checks_total.labels(action=action, result="blocked").inc()
                return False, 0
            rate_limit_checks_total.labels(action=action, result="allowed").inc()
            return True, max_requests

    async def is_allowed(self, key: str) -> bool:
        if self.default_max_requests is None or self.default_window_seconds is None:
            raise ValueError("Default max_requests and window_seconds must be set for is_allowed")
        allowed, _ = await self.check_rate_limit(
            user_id=key,
            action="default",
            max_requests=self.default_max_requests,
            window_seconds=self.default_window_seconds,
        )
        return allowed

    async def check_alert_rate_limit(
        self, key: str, limit: int, ttl: int, *, fallback_mode: str | None = None
    ) -> bool:
        """Check and increment alert rate limit using Fixed Window pattern.

        Uses Lua script for atomicity across distributed workers.
        Different from existing sliding window (ZSET) pattern - this uses INCR+EXPIRE.

        Args:
            key: Redis key for rate limit counter
            limit: Max allowed requests in window
            ttl: Window size in seconds
            fallback_mode: Behavior on Redis error ('allow' or 'deny', default from instance)

        Returns:
            True if request allowed, False if rate limited
        """

        lua_script = """
        local current = redis.call('INCR', KEYS[1])
        if current == 1 then
            redis.call('EXPIRE', KEYS[1], ARGV[1])
        end
        return current
        """

        try:
            result: Any = await cast(Awaitable[int], self.redis.eval(lua_script, 1, key, str(ttl)))
            allowed = int(result) <= limit
            rate_limit_checks_total.labels(
                action="alert_rate_limit", result="allowed" if allowed else "blocked"
            ).inc()
            return allowed
        except Exception as exc:  # pragma: no cover - defensive path
            rate_limit_redis_errors_total.labels(action="alert_rate_limit").inc()
            logger.warning("alert_rate_limit_fallback", extra={"key": key, "error": str(exc)})
            mode = fallback_mode or self.fallback_mode or "deny"
            allowed = mode == "allow"
            rate_limit_checks_total.labels(
                action="alert_rate_limit", result="allowed" if allowed else "blocked"
            ).inc()
            return allowed

    async def check_channel_rate_limit(self, channel: str) -> bool:
        """Check per-channel rate limit (email 100/min, slack 50/min, sms 10/min).

        Raises:
            ValueError: If channel is not a known type (email, slack, sms)
        """
        if channel not in ALERT_RATE_LIMITS["channel"]:
            raise ValueError(f"Unknown channel type: {channel}")

        minute = int(time.time() // 60)
        key = f"alert_ratelimit:channel:{channel}:{minute}"
        limit = int(ALERT_RATE_LIMITS["channel"][channel])
        return await self.check_alert_rate_limit(key, limit, ALERT_RATE_LIMIT_TTL["channel"])

    async def check_recipient_rate_limit(self, recipient_hash: str, channel: str) -> bool:
        """Check per-recipient rate limit (email 5/hr, slack 10/hr, sms 3/hr).

        Raises:
            ValueError: If channel is not a known type (email, slack, sms)
        """
        if channel not in ALERT_RATE_LIMITS["recipient"]:
            raise ValueError(f"Unknown channel type for recipient limit: {channel}")

        hour = int(time.time() // 3600)
        key = f"alert_ratelimit:recipient:{channel}:{recipient_hash}:{hour}"
        limit = int(ALERT_RATE_LIMITS["recipient"][channel])
        return await self.check_alert_rate_limit(key, limit, ALERT_RATE_LIMIT_TTL["recipient"])

    async def check_global_rate_limit(self) -> bool:
        """Check global burst rate limit (500/min total)."""

        minute = int(time.time() // 60)
        key = f"alert_ratelimit:global:{minute}"
        return await self.check_alert_rate_limit(
            key,
            int(ALERT_RATE_LIMITS["global"]),
            ALERT_RATE_LIMIT_TTL["global"],
        )

    async def health_check(self) -> bool:
        try:
            pong = await self.redis.ping()
            return bool(pong)
        except Exception:  # pragma: no cover
            return False


_rate_limiter_singleton: RateLimiter | None = None


def _build_redis_client() -> redis.Redis:
    """Build async Redis client for rate limiting.

    Supports REDIS_URL (preferred) or REDIS_HOST/REDIS_PORT fallback.
    Always uses db=2 for rate limiting to isolate from main data.
    """
    redis_url = os.getenv("REDIS_URL", "")
    if redis_url:
        # Parse REDIS_URL and override db to 2 for rate limiting isolation
        # Format: redis://host:port/db or redis://host:port
        return redis.Redis.from_url(redis_url, db=2, decode_responses=True)  # type: ignore[no-any-return]

    return redis.Redis(
        host=os.getenv("REDIS_HOST", "redis"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        db=2,
        decode_responses=True,
    )


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        logger.warning("invalid_rate_limit_env", extra={"name": name, "value": os.getenv(name)})
        return default


def get_rate_limiter(fallback_mode: str | None = None) -> RateLimiter:
    global _rate_limiter_singleton
    if _rate_limiter_singleton:
        return _rate_limiter_singleton

    mode = (
        fallback_mode
        if fallback_mode is not None
        else os.getenv("RATE_LIMITER_FALLBACK_MODE", "deny")
    )
    max_requests = _int_env("RATE_LIMIT_MAX", 100)
    window_seconds = _int_env("RATE_LIMIT_WINDOW", 60)
    _rate_limiter_singleton = RateLimiter(
        redis_client=_build_redis_client(),
        fallback_mode=mode,
        max_requests=max_requests,
        window_seconds=window_seconds,
    )
    return _rate_limiter_singleton


def rate_limiter_health_check() -> dict[str, Any] | Awaitable[dict[str, Any]]:
    import asyncio

    rl = get_rate_limiter()
    health_check = rl.health_check()

    if asyncio.iscoroutine(health_check):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return {"healthy": asyncio.run(health_check)}

        async def _run() -> dict[str, Any]:
            return {"healthy": await health_check}

        return _run()

    return {"healthy": health_check}


__all__ = [
    "RateLimiter",
    "RedisRateLimiter",
    "get_rate_limiter",
    "rate_limit_checks_total",
    "rate_limit_redis_errors_total",
    "rate_limiter_health_check",
]

# Backwards compatibility alias
RedisRateLimiter = RateLimiter
