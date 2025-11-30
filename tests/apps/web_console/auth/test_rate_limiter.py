"""Tests for Redis-backed rate limiter.

Tests verify sliding window rate limiting with multi-worker safety.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest

from apps.web_console.auth.rate_limiter import RedisRateLimiter


@pytest.fixture()
def mock_redis():
    """Mock Redis async client."""
    redis = AsyncMock()
    redis.zremrangebyscore = AsyncMock()
    redis.zcard = AsyncMock()
    redis.zadd = AsyncMock()
    redis.expire = AsyncMock()
    redis.delete = AsyncMock()
    return redis


class TestRedisRateLimiter:
    """Test Redis rate limiter initialization."""

    def test_rate_limiter_initialization(self, mock_redis):
        """Test rate limiter initializes with correct config."""
        limiter = RedisRateLimiter(
            redis_client=mock_redis,
            max_requests=10,
            window_seconds=60,
            key_prefix="rate_limit:test:",
        )

        assert limiter.redis == mock_redis
        assert limiter.max_requests == 10
        assert limiter.window_seconds == 60
        assert limiter.key_prefix == "rate_limit:test:"


class TestRateLimiting:
    """Test rate limiting behavior."""

    @pytest.mark.asyncio()
    async def test_requests_under_limit_allowed(self, mock_redis):
        """Test requests under limit are allowed."""
        limiter = RedisRateLimiter(
            redis_client=mock_redis,
            max_requests=10,
            window_seconds=60,
            key_prefix="rate_limit:callback:",
        )

        # Mock current count = 5 (under limit)
        mock_redis.zcard.return_value = 5

        result = await limiter.is_allowed("192.168.1.1")

        assert result is True
        mock_redis.zremrangebyscore.assert_called_once()
        mock_redis.zadd.assert_called_once()
        mock_redis.expire.assert_called_once()

    @pytest.mark.asyncio()
    async def test_requests_at_limit_rejected(self, mock_redis):
        """Test requests at or over limit are rejected."""
        limiter = RedisRateLimiter(
            redis_client=mock_redis,
            max_requests=10,
            window_seconds=60,
            key_prefix="rate_limit:callback:",
        )

        # Mock current count = 10 (at limit)
        mock_redis.zcard.return_value = 10

        result = await limiter.is_allowed("192.168.1.1")

        assert result is False
        mock_redis.zremrangebyscore.assert_called_once()
        mock_redis.zadd.assert_not_called()  # Should not add new request

    @pytest.mark.asyncio()
    async def test_sliding_window_cleanup(self, mock_redis):
        """Test expired entries are removed from sliding window."""
        limiter = RedisRateLimiter(
            redis_client=mock_redis,
            max_requests=10,
            window_seconds=60,
            key_prefix="rate_limit:callback:",
        )

        mock_redis.zcard.return_value = 5

        await limiter.is_allowed("192.168.1.1")

        # Verify zremrangebyscore was called to remove expired entries
        mock_redis.zremrangebyscore.assert_called_once()
        call_args = mock_redis.zremrangebyscore.call_args[0]
        assert call_args[0] == "rate_limit:callback:192.168.1.1"
        assert call_args[1] == 0  # Min score

    @pytest.mark.asyncio()
    async def test_key_expiration_set(self, mock_redis):
        """Test Redis key expiration is set to window duration."""
        limiter = RedisRateLimiter(
            redis_client=mock_redis,
            max_requests=10,
            window_seconds=60,
            key_prefix="rate_limit:callback:",
        )

        mock_redis.zcard.return_value = 5

        await limiter.is_allowed("192.168.1.1")

        # Verify expire called with window duration
        mock_redis.expire.assert_called_once_with("rate_limit:callback:192.168.1.1", 60)

    @pytest.mark.asyncio()
    async def test_different_identifiers_independent(self, mock_redis):
        """Test different identifiers have independent rate limits."""
        limiter = RedisRateLimiter(
            redis_client=mock_redis,
            max_requests=10,
            window_seconds=60,
            key_prefix="rate_limit:callback:",
        )

        mock_redis.zcard.return_value = 5

        # First identifier
        await limiter.is_allowed("192.168.1.1")
        first_call_key = mock_redis.zremrangebyscore.call_args[0][0]

        # Second identifier
        await limiter.is_allowed("192.168.1.2")
        second_call_key = mock_redis.zremrangebyscore.call_args[0][0]

        # Verify different keys used
        assert first_call_key == "rate_limit:callback:192.168.1.1"
        assert second_call_key == "rate_limit:callback:192.168.1.2"


class TestResetFunctionality:
    """Test rate limit reset."""

    @pytest.mark.asyncio()
    async def test_reset_clears_rate_limit(self, mock_redis):
        """Test reset clears rate limit for identifier."""
        limiter = RedisRateLimiter(
            redis_client=mock_redis,
            max_requests=10,
            window_seconds=60,
            key_prefix="rate_limit:callback:",
        )

        await limiter.reset("192.168.1.1")

        mock_redis.delete.assert_called_once_with("rate_limit:callback:192.168.1.1")


class TestMultiWorkerScenarios:
    """Test multi-worker rate limiting scenarios."""

    @pytest.mark.asyncio()
    async def test_concurrent_requests_use_redis(self, mock_redis):
        """Test concurrent requests use Redis for coordination."""
        limiter = RedisRateLimiter(
            redis_client=mock_redis,
            max_requests=10,
            window_seconds=60,
            key_prefix="rate_limit:callback:",
        )

        # Simulate concurrent requests from same IP
        mock_redis.zcard.return_value = 5

        # Make multiple concurrent requests
        tasks = [limiter.is_allowed("192.168.1.1") for _ in range(3)]
        results = await asyncio.gather(*tasks)

        # All should be allowed (under limit)
        assert all(results)

        # Verify Redis operations were called for each request
        assert mock_redis.zcard.call_count == 3
        assert mock_redis.zadd.call_count == 3


class TestKeyPrefixes:
    """Test key prefix isolation."""

    @pytest.mark.asyncio()
    async def test_different_prefixes_independent(self, mock_redis):
        """Test different key prefixes create independent rate limits."""
        callback_limiter = RedisRateLimiter(
            redis_client=mock_redis,
            max_requests=10,
            window_seconds=60,
            key_prefix="rate_limit:callback:",
        )

        refresh_limiter = RedisRateLimiter(
            redis_client=mock_redis,
            max_requests=5,
            window_seconds=60,
            key_prefix="rate_limit:refresh:",
        )

        mock_redis.zcard.return_value = 5

        # Same identifier, different prefixes
        await callback_limiter.is_allowed("user123")
        callback_key = mock_redis.zremrangebyscore.call_args[0][0]

        await refresh_limiter.is_allowed("user123")
        refresh_key = mock_redis.zremrangebyscore.call_args[0][0]

        # Verify different keys used
        assert callback_key == "rate_limit:callback:user123"
        assert refresh_key == "rate_limit:refresh:user123"
