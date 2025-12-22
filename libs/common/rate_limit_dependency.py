"""FastAPI rate limiting dependency for API endpoints.

Provides per-user and global rate limiting using Redis sliding window.
Uses Redis TIME for clock skew prevention across distributed instances.

CRITICAL: C5 must remain in log_only mode until C6 (API Authentication) is deployed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import redis.asyncio as redis
from fastapi import HTTPException, Request, Response
from prometheus_client import Counter

from libs.web_console_auth.rate_limiter import (
    get_rate_limiter,
    rate_limit_redis_errors_total,
)

logger = logging.getLogger(__name__)

# NEW metrics with different names to avoid conflict with existing rate_limit_checks_total
rate_limit_api_checks_total = Counter(
    "rate_limit_api_checks_total",
    "API rate limit checks (order/signal endpoints)",
    ["action", "result", "principal_type"],
)

rate_limit_bypass_total = Counter(
    "rate_limit_bypass_total",
    "Rate limit bypasses for internal services",
    ["method"],
)

rate_limit_redis_timeout_total = Counter(
    "rate_limit_redis_timeout_total",
    "Redis timeouts during rate limit checks",
    ["action"],
)

# Redis latency threshold for circuit breaker
REDIS_LATENCY_THRESHOLD_MS = int(os.getenv("RATE_LIMIT_REDIS_LATENCY_THRESHOLD", "50"))


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting an action."""

    action: str
    max_requests: int
    window_seconds: int = 60
    burst_buffer: int = 0  # Extra allowance (effectively raises limit)
    fallback_mode: str = "deny"  # "deny" or "allow"
    global_limit: int | None = None  # Optional global cap across all users
    anonymous_factor: float = 0.1  # Multiplier for anonymous traffic


# Lua script using Redis TIME to avoid clock skew
_RATE_LIMIT_WITH_GLOBAL_SCRIPT = """
-- Use Redis server time to avoid clock skew
local redis_time = redis.call('TIME')
local now = tonumber(redis_time[1])

local key = KEYS[1]
local global_key = KEYS[2]
local window = tonumber(ARGV[1])
local max_requests = tonumber(ARGV[2])
local global_limit = tonumber(ARGV[3])
local member = ARGV[4]

-- Check global limit first
if global_key ~= "" and global_limit > 0 then
    redis.call('ZADD', global_key, now, member)
    redis.call('ZREMRANGEBYSCORE', global_key, 0, now - window)
    local global_count = redis.call('ZCARD', global_key)
    redis.call('EXPIRE', global_key, window)
    if global_count > global_limit then
        return {-1, global_count, now}  -- Global limit exceeded
    end
end

-- Check per-user limit
redis.call('ZADD', key, now, member)
redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
local count = redis.call('ZCARD', key)
redis.call('EXPIRE', key, window)

return {count, 0, now}
"""


def _get_rate_limit_mode() -> str:
    """Get current rate limit mode. Read per-request for hot-switch support."""
    return os.getenv("RATE_LIMIT_MODE", "log_only")  # Default to log_only until C6 deployed


def _get_redis_client() -> redis.Redis:
    """Get async Redis client with SAME configuration as existing rate limiter.

    Note: redis.Redis here refers to redis.asyncio.Redis due to the import alias
    at the top of this file (import redis.asyncio as redis).
    Uses DB 2, decode_responses=True, same connection settings.
    """
    limiter = get_rate_limiter()
    return limiter.redis


def get_principal_key(request: Request) -> tuple[str, str]:
    """Extract principal key for rate limiting.

    SECURITY: Only use verified identity from request.state.user (set by auth middleware).
    Never decode unverified JWT claims - this could allow bucket evasion.

    Returns:
        Tuple of (key, principal_type) for metrics labeling.

    Priority order (highest to lowest):
    1. Authenticated user ID from request.state.user (verified by auth middleware)
    2. S2S internal service with user context (C6: rate limit by acting user)
    3. Strategy ID from request.state.strategy (verified by S2S auth)
    4. Service ID from verified internal token (C6: service-level rate limiting)
    5. IP address as last resort (unauthenticated endpoints only)
    """
    # Authenticated user from verified session/JWT (auth middleware sets this)
    if hasattr(request.state, "user") and request.state.user:
        user = request.state.user
        user_id = user.get("user_id") if isinstance(user, dict) else getattr(user, "user_id", None)
        if not user_id:
            user_id = user.get("sub") if isinstance(user, dict) else getattr(user, "sub", None)
        if user_id:
            return f"user:{user_id}", "user"

    # C6: Internal service with verified token - check for user context first
    if getattr(request.state, "internal_service_verified", False):
        # If S2S call has user context, use it for rate limiting (audit trail)
        if hasattr(request.state, "user") and request.state.user:
            user = request.state.user
            user_id = user.get("user_id") if isinstance(user, dict) else getattr(user, "user_id", None)
            if user_id:
                return f"user:{user_id}", "user"

        # S2S call without user context - use strategy if available
        if hasattr(request.state, "strategy_id") and request.state.strategy_id:
            return f"strategy:{request.state.strategy_id}", "strategy"

        # Fallback to service-level rate limiting
        service_id = getattr(request.state, "service_id", "unknown")
        return f"service:{service_id}", "service"

    # Strategy ID from verified S2S auth (set by internal auth middleware)
    if hasattr(request.state, "strategy_id") and request.state.strategy_id:
        return f"strategy:{request.state.strategy_id}", "strategy"

    # Fallback to IP (only for truly unauthenticated endpoints)
    # NOTE: Requires ProxyHeadersMiddleware for accurate client IP
    if request.client:
        return f"ip:{request.client.host}", "ip"

    return "ip:unknown", "ip"


def is_internal_service(request: Request) -> tuple[bool, str]:
    """Check if request is from trusted internal service.

    SECURITY: Only verified identity methods allowed.
    - mTLS client cert (request.state.mtls_verified)
    - JWT with 'internal-service' audience (request.state.user.aud)
    - Verified internal token (request.state.internal_service_verified) - C6

    Static bypass tokens are NOT allowed due to abuse risk.

    Returns:
        Tuple of (is_internal, method) where method is the verification method used.
    """
    # Check for mTLS verified internal service
    if getattr(request.state, "mtls_verified", False):
        if getattr(request.state, "mtls_service_name", None):
            return True, "mtls"

    # Check for internal service JWT audience (verified by auth middleware)
    if hasattr(request.state, "user") and request.state.user:
        user = request.state.user
        aud = user.get("aud") if isinstance(user, dict) else getattr(user, "aud", None)
        if aud == "internal-service":
            return True, "jwt_audience"

    # C6: Check for verified internal token (set by api_auth dependency)
    if getattr(request.state, "internal_service_verified", False):
        return True, "internal_token"

    return False, ""


def should_bypass_rate_limit(request: Request) -> bool:
    """Check if request should completely bypass rate limiting.

    DEPRECATED: Internal services no longer fully bypass rate limits.
    They get higher limits via INTERNAL_SERVICE_FACTOR instead.

    Currently only mTLS-verified services bypass for backwards compatibility,
    but they should eventually use the internal service factor as well.

    This function is kept for compatibility but will be removed in a future release.
    """
    # Only mTLS still bypasses completely (legacy - to be migrated)
    if getattr(request.state, "mtls_verified", False):
        if getattr(request.state, "mtls_service_name", None):
            rate_limit_bypass_total.labels(method="mtls").inc()
            return True

    return False


# Factor applied to limits for internal services (e.g., 10x higher limits)
# This prevents runaway internal loops while still allowing normal S2S traffic
INTERNAL_SERVICE_FACTOR = float(os.getenv("RATE_LIMIT_INTERNAL_FACTOR", "10.0"))


async def check_rate_limit_with_global(
    redis_client: redis.Redis,
    user_id: str,
    action: str,
    config: RateLimitConfig,
) -> tuple[bool, int, str]:
    """Check rate limit with global cap using Redis server time.

    Note: redis_client is redis.asyncio.Redis (see import alias at top of file).
    Uses the SAME Redis client settings as existing rate limiter (DB 2, decode_responses=True).

    Returns:
        Tuple of (allowed, remaining, rejection_reason)
    """
    key = f"rl:{action}:{user_id}"
    global_key = f"rl:{action}:global" if config.global_limit else ""
    member = f"{user_id}:{time.time_ns()}"  # Unique member per request

    effective_limit = config.max_requests + config.burst_buffer

    # redis.Redis here is redis.asyncio.Redis (see import), so eval() returns Awaitable
    result: Any = await redis_client.eval(  # type: ignore[misc]
        _RATE_LIMIT_WITH_GLOBAL_SCRIPT,
        2,
        key,
        global_key,
        str(config.window_seconds),
        str(effective_limit),
        str(config.global_limit or 0),
        member,
    )

    count, global_flag, _redis_time = result
    if count == -1:
        return False, 0, "global_limit_exceeded"

    allowed = int(count) <= effective_limit
    remaining = max(0, effective_limit - int(count))
    reason = "" if allowed else "per_user_limit_exceeded"
    return allowed, remaining, reason


async def check_rate_limit_with_circuit_breaker(
    user_id: str,
    action: str,
    config: RateLimitConfig,
) -> tuple[bool, int, str]:
    """Check rate limit with circuit breaker for Redis latency."""
    redis_client = _get_redis_client()
    try:
        result = await asyncio.wait_for(
            check_rate_limit_with_global(redis_client, user_id, action, config),
            timeout=REDIS_LATENCY_THRESHOLD_MS / 1000,
        )
        return result
    except TimeoutError:
        rate_limit_redis_timeout_total.labels(action=action).inc()
        logger.warning("rate_limit_redis_timeout", extra={"action": action})
        if config.fallback_mode == "deny":
            return False, 0, "redis_timeout"
        return True, config.max_requests, ""
    except Exception as exc:
        rate_limit_redis_errors_total.labels(action=action).inc()
        logger.error("rate_limit_redis_error", extra={"action": action, "error": str(exc)})
        if config.fallback_mode == "deny":
            return False, 0, "redis_error"
        return True, config.max_requests, ""


def rate_limit(config: RateLimitConfig) -> Callable[..., Awaitable[int]]:
    """FastAPI dependency for rate limiting."""

    async def dependency(request: Request, response: Response) -> int:
        effective_limit = config.max_requests + config.burst_buffer

        # Check for full bypass (mTLS only - legacy, to be migrated)
        if should_bypass_rate_limit(request):
            # Still emit headers even for bypass
            response.headers["X-RateLimit-Limit"] = str(effective_limit)
            response.headers["X-RateLimit-Remaining"] = str(effective_limit)
            response.headers["X-RateLimit-Window"] = str(config.window_seconds)
            return effective_limit

        key, principal_type = get_principal_key(request)

        # Check if internal service (gets higher limits, not bypass)
        is_internal, internal_method = is_internal_service(request)

        # Apply limit factors based on principal type
        effective_config = config
        if is_internal:
            # Internal services get higher per-user limits (configurable via RATE_LIMIT_INTERNAL_FACTOR)
            # NOTE: global_limit is NOT scaled - it protects downstream systems (broker API)
            rate_limit_bypass_total.labels(method=internal_method).inc()
            effective_config = RateLimitConfig(
                action=config.action,
                max_requests=int(config.max_requests * INTERNAL_SERVICE_FACTOR),
                window_seconds=config.window_seconds,
                burst_buffer=int(config.burst_buffer * INTERNAL_SERVICE_FACTOR),
                fallback_mode=config.fallback_mode,
                global_limit=config.global_limit,  # Keep original - protects downstream systems
                anonymous_factor=config.anonymous_factor,
            )
            effective_limit = effective_config.max_requests + effective_config.burst_buffer
        elif principal_type == "ip":
            # Anonymous/IP-based gets lower limits
            effective_config = RateLimitConfig(
                action=config.action,
                max_requests=int(config.max_requests * config.anonymous_factor),
                window_seconds=config.window_seconds,
                burst_buffer=int(config.burst_buffer * config.anonymous_factor),
                fallback_mode=config.fallback_mode,
                global_limit=config.global_limit,
                anonymous_factor=config.anonymous_factor,
            )
            effective_limit = effective_config.max_requests + effective_config.burst_buffer

        allowed, remaining, rejection_reason = await check_rate_limit_with_circuit_breaker(
            user_id=key,
            action=config.action,
            config=effective_config,
        )

        # ALWAYS add response headers (both log_only and enforce modes)
        response.headers["X-RateLimit-Limit"] = str(effective_limit)
        response.headers["X-RateLimit-Remaining"] = str(max(0, remaining))
        response.headers["X-RateLimit-Window"] = str(effective_config.window_seconds)

        # ALWAYS emit metrics (both log_only and enforce modes)
        rate_limit_api_checks_total.labels(
            action=config.action,
            result="blocked" if not allowed else "allowed",
            principal_type=principal_type,
        ).inc()

        if not allowed:
            mode = _get_rate_limit_mode()  # Read per-request
            logger.warning(
                "rate_limit_exceeded",
                extra={
                    "action": config.action,
                    "key": key,
                    "principal_type": principal_type,
                    "mode": mode,
                    "rejection_reason": rejection_reason,
                    "request_id": getattr(request.state, "request_id", None),
                },
            )

            if mode == "enforce":
                raise HTTPException(
                    status_code=429,
                    detail={
                        "error": "rate_limited",
                        "message": "Too many requests",
                        "retry_after": effective_config.window_seconds,
                        "reason": rejection_reason,
                    },
                    headers={"Retry-After": str(effective_config.window_seconds)},
                )

        return remaining

    return dependency


__all__ = [
    "RateLimitConfig",
    "rate_limit",
    "rate_limit_api_checks_total",
    "rate_limit_bypass_total",
    "rate_limit_redis_timeout_total",
    "get_principal_key",
    "should_bypass_rate_limit",
    "is_internal_service",
    "INTERNAL_SERVICE_FACTOR",
]
