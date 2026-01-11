"""Tests for alert worker entrypoint exception handling."""

from unittest.mock import AsyncMock

import psycopg
import pytest
import redis.exceptions

from apps.alert_worker.entrypoint import AsyncResources, _close_async_resources


class TestCloseAsyncResources:
    """Test _close_async_resources exception handling."""

    @pytest.mark.asyncio()
    async def test_close_both_succeed(self):
        """Test successful close of both resources."""
        mock_db_pool = AsyncMock()
        mock_redis = AsyncMock()
        mock_poison_queue = AsyncMock()
        mock_rate_limiter = AsyncMock()

        resources = AsyncResources(
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
            rate_limiter=mock_rate_limiter,
        )

        # Should not raise
        await _close_async_resources(resources)

        mock_db_pool.close.assert_awaited_once()
        mock_redis.close.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_db_pool_close_psycopg_error_propagates(self):
        """Test psycopg error during DB pool close is propagated."""
        mock_db_pool = AsyncMock()
        mock_redis = AsyncMock()
        mock_poison_queue = AsyncMock()
        mock_rate_limiter = AsyncMock()

        # Mock DB pool close to raise psycopg.Error
        db_error = psycopg.OperationalError("DB connection failed")
        mock_db_pool.close.side_effect = db_error

        resources = AsyncResources(
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
            rate_limiter=mock_rate_limiter,
        )

        # Should propagate the error
        with pytest.raises(psycopg.OperationalError):
            await _close_async_resources(resources)

        # Verify both close attempts were made
        mock_db_pool.close.assert_awaited_once()
        mock_redis.close.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_redis_close_redis_error_propagates(self):
        """Test Redis error during Redis close is propagated."""
        mock_db_pool = AsyncMock()
        mock_redis = AsyncMock()
        mock_poison_queue = AsyncMock()
        mock_rate_limiter = AsyncMock()

        # Mock Redis close to raise redis.exceptions.RedisError
        redis_error = redis.exceptions.ConnectionError("Redis connection failed")
        mock_redis.close.side_effect = redis_error

        resources = AsyncResources(
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
            rate_limiter=mock_rate_limiter,
        )

        # Should propagate the error
        with pytest.raises(redis.exceptions.ConnectionError):
            await _close_async_resources(resources)

        # Verify both close attempts were made
        mock_db_pool.close.assert_awaited_once()
        mock_redis.close.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_both_close_fail_raises_exception_group(self):
        """Test both resources failing raises ExceptionGroup."""
        mock_db_pool = AsyncMock()
        mock_redis = AsyncMock()
        mock_poison_queue = AsyncMock()
        mock_rate_limiter = AsyncMock()

        # Mock both to raise errors
        db_error = psycopg.OperationalError("DB connection failed")
        redis_error = redis.exceptions.ConnectionError("Redis connection failed")
        mock_db_pool.close.side_effect = db_error
        mock_redis.close.side_effect = redis_error

        resources = AsyncResources(
            db_pool=mock_db_pool,
            redis_client=mock_redis,
            poison_queue=mock_poison_queue,
            rate_limiter=mock_rate_limiter,
        )

        # Should raise ExceptionGroup with both errors
        with pytest.raises(ExceptionGroup) as exc_info:
            await _close_async_resources(resources)

        # Verify exception group contains both errors
        assert len(exc_info.value.exceptions) == 2
        assert isinstance(exc_info.value.exceptions[0], psycopg.OperationalError)
        assert isinstance(exc_info.value.exceptions[1], redis.exceptions.ConnectionError)

        # Verify both close attempts were made
        mock_db_pool.close.assert_awaited_once()
        mock_redis.close.assert_awaited_once()
