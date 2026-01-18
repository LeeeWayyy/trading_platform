"""Unit tests for libs.platform.web_console_auth.rate_limiter."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
import redis.exceptions

from libs.platform.web_console_auth.rate_limiter import RateLimiter


@pytest.mark.asyncio()
async def test_check_rate_limit_allows_and_remaining() -> None:
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(return_value=3)
    limiter = RateLimiter(redis_client, fallback_mode="deny")

    with patch("libs.platform.web_console_auth.rate_limiter.time.time", return_value=123):
        with patch("libs.platform.web_console_auth.rate_limiter.time.time_ns", return_value=999):
            allowed, remaining = await limiter.check_rate_limit(
                user_id="user1",
                action="login",
                max_requests=5,
                window_seconds=60,
            )

    assert allowed is True
    assert remaining == 2
    args = redis_client.eval.call_args.args
    assert args[2] == "rl:login:user1"
    assert args[3] == "123"
    assert args[4] == "60"
    assert args[5] == "5"
    assert args[6] == "user1:999"


@pytest.mark.asyncio()
async def test_check_rate_limit_blocks_when_exceeded() -> None:
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(return_value=10)
    limiter = RateLimiter(redis_client, fallback_mode="deny")

    allowed, remaining = await limiter.check_rate_limit(
        user_id="user2",
        action="search",
        max_requests=5,
        window_seconds=60,
    )

    assert allowed is False
    assert remaining == 0


@pytest.mark.asyncio()
async def test_check_rate_limit_fallback_deny_on_connection_error() -> None:
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(side_effect=redis.exceptions.ConnectionError("down"))
    limiter = RateLimiter(redis_client, fallback_mode="deny")

    allowed, remaining = await limiter.check_rate_limit(
        user_id="user3",
        action="write",
        max_requests=7,
        window_seconds=30,
    )

    assert allowed is False
    assert remaining == 0


@pytest.mark.asyncio()
async def test_check_rate_limit_fallback_allow_on_timeout() -> None:
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(side_effect=redis.exceptions.TimeoutError("timeout"))
    limiter = RateLimiter(redis_client, fallback_mode="allow")

    allowed, remaining = await limiter.check_rate_limit(
        user_id="user4",
        action="read",
        max_requests=7,
        window_seconds=30,
    )

    assert allowed is True
    assert remaining == 7


@pytest.mark.asyncio()
async def test_check_alert_rate_limit_allows_when_under_limit() -> None:
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(return_value=2)
    limiter = RateLimiter(redis_client, fallback_mode="deny")

    allowed = await limiter.check_alert_rate_limit("alert:key", limit=3, ttl=60)

    assert allowed is True


@pytest.mark.asyncio()
async def test_check_alert_rate_limit_blocks_when_exceeded() -> None:
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(return_value=4)
    limiter = RateLimiter(redis_client, fallback_mode="deny")

    allowed = await limiter.check_alert_rate_limit("alert:key", limit=3, ttl=60)

    assert allowed is False


@pytest.mark.asyncio()
async def test_check_alert_rate_limit_fallback_allow_on_redis_error() -> None:
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(side_effect=redis.exceptions.ConnectionError("down"))
    limiter = RateLimiter(redis_client, fallback_mode="allow")

    allowed = await limiter.check_alert_rate_limit("alert:key", limit=3, ttl=60)

    assert allowed is True


@pytest.mark.asyncio()
async def test_check_channel_rate_limit_invalid_channel() -> None:
    redis_client = AsyncMock()
    limiter = RateLimiter(redis_client, fallback_mode="deny")

    with pytest.raises(ValueError, match="Unknown channel"):
        await limiter.check_channel_rate_limit("fax")


@pytest.mark.asyncio()
async def test_health_check_success_and_failure() -> None:
    redis_client = AsyncMock()
    redis_client.ping = AsyncMock(return_value=True)
    limiter = RateLimiter(redis_client, fallback_mode="deny")

    assert await limiter.health_check() is True

    redis_client.ping = AsyncMock(side_effect=redis.exceptions.ConnectionError("down"))
    assert await limiter.health_check() is False


@pytest.mark.asyncio()
async def test_is_allowed_requires_defaults() -> None:
    redis_client = AsyncMock()
    limiter = RateLimiter(redis_client)

    with pytest.raises(ValueError, match="Default max_requests and window_seconds must be set"):
        await limiter.is_allowed("any")
