"""
Unit tests for libs.core.common.sync_db_pool.

Tests cover:
- Redis URL building (from env vars, with fallbacks)
- Sync DB pool creation (success and error cases)
- Sync Redis client creation
- Job queue context manager
- Singleton behavior (lru_cache)

Target: 50%+ branch coverage (baseline from 0%)
"""

import os
from unittest.mock import Mock, patch

import pytest

from libs.core.common.sync_db_pool import (
    _get_redis_url,
    get_job_queue,
    get_sync_db_pool,
    get_sync_redis_client,
)


class TestRedisUrlBuilding:
    """Tests for Redis URL construction from environment variables."""

    def test_get_redis_url_from_redis_url_env(self):
        """Test Redis URL uses REDIS_URL env var when set."""
        with patch.dict(os.environ, {"REDIS_URL": "redis://custom-host:6380/1"}):
            url = _get_redis_url()

            assert url == "redis://custom-host:6380/1"

    def test_get_redis_url_from_individual_env_vars(self):
        """Test Redis URL built from REDIS_HOST/PORT/DB when REDIS_URL not set."""
        with patch.dict(
            os.environ,
            {
                "REDIS_HOST": "my-redis-host",
                "REDIS_PORT": "6380",
                "REDIS_DB": "2",
            },
            clear=True,  # Clear REDIS_URL
        ):
            url = _get_redis_url()

            assert url == "redis://my-redis-host:6380/2"

    def test_get_redis_url_with_defaults(self):
        """Test Redis URL uses defaults when no env vars set."""
        with patch.dict(os.environ, {}, clear=True):
            url = _get_redis_url()

            # Default: redis://redis:6379/0 (container-compatible)
            assert url == "redis://redis:6379/0"

    def test_get_redis_url_with_partial_env_vars(self):
        """Test Redis URL uses mix of env vars and defaults."""
        with patch.dict(
            os.environ,
            {"REDIS_HOST": "prod-redis"},  # Only host set, port/db use defaults
            clear=True,
        ):
            url = _get_redis_url()

            assert url == "redis://prod-redis:6379/0"


class TestSyncDbPool:
    """Tests for synchronous database connection pool."""

    @patch("libs.core.common.sync_db_pool.ConnectionPool")
    def test_get_sync_db_pool_success(self, mock_pool_class):
        """Test sync DB pool created successfully when DATABASE_URL set."""
        mock_pool = Mock()
        mock_pool_class.return_value = mock_pool

        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://localhost/testdb"}):
            # Clear lru_cache to ensure fresh call
            get_sync_db_pool.cache_clear()

            pool = get_sync_db_pool()

            # Verify pool created with correct parameters
            mock_pool_class.assert_called_once_with(
                conninfo="postgresql://localhost/testdb",
                min_size=1,
                max_size=5,
            )
            mock_pool.open.assert_called_once()
            assert pool is mock_pool

    def test_get_sync_db_pool_missing_database_url(self):
        """Test sync DB pool raises RuntimeError when DATABASE_URL not set."""
        with patch.dict(os.environ, {}, clear=True):
            # Clear lru_cache to ensure fresh call
            get_sync_db_pool.cache_clear()

            with pytest.raises(RuntimeError, match="DATABASE_URL not configured"):
                get_sync_db_pool()

    @patch("libs.core.common.sync_db_pool.ConnectionPool")
    def test_get_sync_db_pool_singleton_behavior(self, mock_pool_class):
        """Test sync DB pool uses lru_cache singleton pattern."""
        mock_pool = Mock()
        mock_pool_class.return_value = mock_pool

        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://localhost/testdb"}):
            # Clear lru_cache to start fresh
            get_sync_db_pool.cache_clear()

            # Call twice
            pool1 = get_sync_db_pool()
            pool2 = get_sync_db_pool()

            # Verify singleton: ConnectionPool created only once
            assert mock_pool_class.call_count == 1
            assert pool1 is pool2


class TestSyncRedisClient:
    """Tests for synchronous Redis client."""

    @patch("libs.core.common.sync_db_pool.redis.Redis.from_url")
    def test_get_sync_redis_client_success(self, mock_from_url):
        """Test sync Redis client created from Redis URL."""
        mock_client = Mock()
        mock_from_url.return_value = mock_client

        with patch.dict(os.environ, {"REDIS_URL": "redis://test-host:6379/0"}):
            # Clear lru_cache to ensure fresh call
            get_sync_redis_client.cache_clear()

            client = get_sync_redis_client()

            # Verify Redis client created from URL
            mock_from_url.assert_called_once_with("redis://test-host:6379/0")
            assert client is mock_client

    @patch("libs.core.common.sync_db_pool.redis.Redis.from_url")
    def test_get_sync_redis_client_with_defaults(self, mock_from_url):
        """Test sync Redis client uses default URL when no env vars set."""
        mock_client = Mock()
        mock_from_url.return_value = mock_client

        with patch.dict(os.environ, {}, clear=True):
            # Clear lru_cache to ensure fresh call
            get_sync_redis_client.cache_clear()

            client = get_sync_redis_client()

            # Verify Redis client created with default URL
            mock_from_url.assert_called_once_with("redis://redis:6379/0")
            assert client is mock_client

    @patch("libs.core.common.sync_db_pool.redis.Redis.from_url")
    def test_get_sync_redis_client_singleton_behavior(self, mock_from_url):
        """Test sync Redis client uses lru_cache singleton pattern."""
        mock_client = Mock()
        mock_from_url.return_value = mock_client

        with patch.dict(os.environ, {"REDIS_URL": "redis://test:6379/0"}):
            # Clear lru_cache to start fresh
            get_sync_redis_client.cache_clear()

            # Call twice
            client1 = get_sync_redis_client()
            client2 = get_sync_redis_client()

            # Verify singleton: from_url called only once
            assert mock_from_url.call_count == 1
            assert client1 is client2


class TestJobQueueContextManager:
    """Tests for BacktestJobQueue context manager."""

    @patch("libs.trading.backtest.job_queue.BacktestJobQueue")
    @patch("libs.core.common.sync_db_pool.get_sync_db_pool")
    @patch("libs.core.common.sync_db_pool.get_sync_redis_client")
    def test_get_job_queue_yields_queue(
        self, mock_get_redis, mock_get_pool, mock_queue_class
    ):
        """Test job queue context manager yields BacktestJobQueue instance."""
        mock_redis = Mock()
        mock_pool = Mock()
        mock_queue = Mock()

        mock_get_redis.return_value = mock_redis
        mock_get_pool.return_value = mock_pool
        mock_queue_class.return_value = mock_queue

        with get_job_queue() as queue:
            # Verify queue created with correct dependencies
            mock_queue_class.assert_called_once_with(mock_redis, mock_pool)
            assert queue is mock_queue

    @patch("libs.trading.backtest.job_queue.BacktestJobQueue")
    @patch("libs.core.common.sync_db_pool.get_sync_db_pool")
    @patch("libs.core.common.sync_db_pool.get_sync_redis_client")
    def test_get_job_queue_cleanup_does_not_close_pool(
        self, mock_get_redis, mock_get_pool, mock_queue_class
    ):
        """Test job queue cleanup does not close singleton pool."""
        mock_redis = Mock()
        mock_pool = Mock()
        mock_queue = Mock()

        mock_get_redis.return_value = mock_redis
        mock_get_pool.return_value = mock_pool
        mock_queue_class.return_value = mock_queue

        with get_job_queue():
            pass

        # Verify pool.close() NOT called (singleton reuse)
        mock_pool.close.assert_not_called()

    @patch("libs.trading.backtest.job_queue.BacktestJobQueue")
    @patch("libs.core.common.sync_db_pool.get_sync_db_pool")
    @patch("libs.core.common.sync_db_pool.get_sync_redis_client")
    def test_get_job_queue_exception_handling(
        self, mock_get_redis, mock_get_pool, mock_queue_class
    ):
        """Test job queue context manager handles exceptions correctly."""
        mock_redis = Mock()
        mock_pool = Mock()
        mock_queue = Mock()

        mock_get_redis.return_value = mock_redis
        mock_get_pool.return_value = mock_pool
        mock_queue_class.return_value = mock_queue

        # Simulate exception within context
        with pytest.raises(ValueError, match="Test exception"):
            with get_job_queue():
                raise ValueError("Test exception")

        # Verify cleanup still occurred (pool not closed)
        mock_pool.close.assert_not_called()
