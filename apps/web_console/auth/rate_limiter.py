"""Redis sliding-window rate limiter for web console auth endpoints."""

from __future__ import annotations

import logging
import os
import time
from collections.abc import Awaitable
from typing import Any

import redis.asyncio as redis
from prometheus_client import Counter

logger = logging.getLogger(__name__)

rate_limit_checks_total = Counter(
    "rate_limit_checks_total", "Total rate limit checks", ["action", "result"]
)
rate_limit_redis_errors_total = Counter(
    "rate_limit_redis_errors_total", "Redis errors during rate limit checks", ["action"]
)


class RateLimiter:
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

    # Lua script for atomic sliding window rate limiting
    # Ensures zadd, zremrangebyscore, zcard, and expire run atomically
    _RATE_LIMIT_SCRIPT = """
    local key = KEYS[1]
    local now = tonumber(ARGV[1])
    local window = tonumber(ARGV[2])
    local max_requests = tonumber(ARGV[3])
    local member = ARGV[4]

    -- Add new request
    redis.call('ZADD', key, now, member)
    -- Remove old entries outside window
    redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
    -- Count current requests in window
    local count = redis.call('ZCARD', key)
    -- Set expiry
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
            # Use Lua script for atomic execution
            # Convert int args to str for mypy (redis.eval expects *str args)
            eval_result = self.redis.eval(
                self._RATE_LIMIT_SCRIPT,
                1,  # number of keys
                key,  # KEYS[1]
                str(now),  # ARGV[1]
                str(window_seconds),  # ARGV[2]
                str(max_requests),  # ARGV[3]
                member,  # ARGV[4]
            )
            # redis.eval returns Awaitable[str] | str; handle both for async client
            if hasattr(eval_result, "__await__"):
                result = await eval_result
            else:
                result = eval_result
            count = int(result)  # Lua script returns count as int
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

    async def health_check(self) -> bool:
        try:
            pong = await self.redis.ping()
            return bool(pong)
        except Exception:  # pragma: no cover
            return False


_rate_limiter_singleton: RateLimiter | None = None


def _build_redis_client() -> redis.Redis:
    return redis.Redis(
        host=os.getenv("REDIS_HOST", "redis"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        db=2,  # dedicated DB for rate limiting
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

# Backwards compatibility for auth_service dependency imports
RedisRateLimiter = RateLimiter
