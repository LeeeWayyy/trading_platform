"""
Unit tests for libs.core.common.db_pool.

Tests cover:
- AsyncConnectionAdapter initialization and connection creation
- AsyncRedisAdapter initialization, client creation, and proxy methods
- get_db_pool() factory (success, missing DATABASE_URL, exceptions)
- get_redis_client() factory (success, missing REDIS_URL, exceptions)
- Singleton behavior (lru_cache)

Target: 50%+ branch coverage (baseline from 0%)
"""

import os
from unittest.mock import AsyncMock, Mock, patch

import pytest

from libs.core.common.db_pool import (
    AsyncConnectionAdapter,
    AsyncRedisAdapter,
    get_db_pool,
    get_redis_client,
)


class TestAsyncConnectionAdapter:
    """Tests for AsyncConnectionAdapter initialization."""

    def test_async_connection_adapter_init(self):
        """Test AsyncConnectionAdapter stores configuration correctly."""
        adapter = AsyncConnectionAdapter(
            database_url="postgresql://localhost/testdb",
            connect_timeout=10.0,
        )

        assert adapter._database_url == "postgresql://localhost/testdb"
        assert adapter._connect_timeout == 10.0

    def test_async_connection_adapter_default_timeout(self):
        """Test AsyncConnectionAdapter uses default timeout."""
        adapter = AsyncConnectionAdapter(database_url="postgresql://localhost/testdb")

        assert adapter._connect_timeout == 5.0  # Default


class TestAsyncRedisAdapter:
    """Tests for AsyncRedisAdapter initialization and proxy methods."""

    def test_async_redis_adapter_init(self):
        """Test AsyncRedisAdapter stores configuration correctly."""
        adapter = AsyncRedisAdapter(redis_url="redis://localhost:6379", db=5)

        assert adapter._redis_url == "redis://localhost:6379"
        assert adapter._db == 5

    def test_async_redis_adapter_default_db(self):
        """Test AsyncRedisAdapter uses default db index."""
        adapter = AsyncRedisAdapter(redis_url="redis://localhost:6379")

        assert adapter._db == 3  # Default

    @pytest.mark.asyncio()
    async def test_async_redis_adapter_get(self):
        """Test AsyncRedisAdapter.get() proxy method."""
        with patch("redis.asyncio.Redis.from_url") as mock_from_url:
            mock_client = AsyncMock()
            mock_client.get.return_value = b"test_value"
            mock_client.aclose = AsyncMock()
            mock_from_url.return_value = mock_client

            adapter = AsyncRedisAdapter(redis_url="redis://localhost:6379", db=3)
            result = await adapter.get("test_key")

            assert result == b"test_value"
            mock_client.get.assert_called_once_with("test_key")
            mock_client.aclose.assert_called_once()

    @pytest.mark.asyncio()
    async def test_async_redis_adapter_set(self):
        """Test AsyncRedisAdapter.set() proxy method."""
        with patch("redis.asyncio.Redis.from_url") as mock_from_url:
            mock_client = AsyncMock()
            mock_client.set.return_value = True
            mock_client.aclose = AsyncMock()
            mock_from_url.return_value = mock_client

            adapter = AsyncRedisAdapter(redis_url="redis://localhost:6379", db=3)
            result = await adapter.set("test_key", "test_value", ex=60)

            assert result is True
            mock_client.set.assert_called_once_with("test_key", "test_value", ex=60)
            mock_client.aclose.assert_called_once()

    @pytest.mark.asyncio()
    async def test_async_redis_adapter_setex(self):
        """Test AsyncRedisAdapter.setex() proxy method."""
        with patch("redis.asyncio.Redis.from_url") as mock_from_url:
            mock_client = AsyncMock()
            mock_client.setex.return_value = True
            mock_client.aclose = AsyncMock()
            mock_from_url.return_value = mock_client

            adapter = AsyncRedisAdapter(redis_url="redis://localhost:6379", db=3)
            result = await adapter.setex("test_key", 120, "test_value")

            assert result is True
            mock_client.setex.assert_called_once_with("test_key", 120, "test_value")
            mock_client.aclose.assert_called_once()

    @pytest.mark.asyncio()
    async def test_async_redis_adapter_delete(self):
        """Test AsyncRedisAdapter.delete() proxy method."""
        with patch("redis.asyncio.Redis.from_url") as mock_from_url:
            mock_client = AsyncMock()
            mock_client.delete.return_value = 2  # 2 keys deleted
            mock_client.aclose = AsyncMock()
            mock_from_url.return_value = mock_client

            adapter = AsyncRedisAdapter(redis_url="redis://localhost:6379", db=3)
            result = await adapter.delete("key1", "key2")

            assert result == 2
            mock_client.delete.assert_called_once_with("key1", "key2")
            mock_client.aclose.assert_called_once()

    @pytest.mark.asyncio()
    async def test_async_redis_adapter_exists(self):
        """Test AsyncRedisAdapter.exists() proxy method."""
        with patch("redis.asyncio.Redis.from_url") as mock_from_url:
            mock_client = AsyncMock()
            mock_client.exists.return_value = 1  # 1 key exists
            mock_client.aclose = AsyncMock()
            mock_from_url.return_value = mock_client

            adapter = AsyncRedisAdapter(redis_url="redis://localhost:6379", db=3)
            result = await adapter.exists("test_key")

            assert result == 1
            mock_client.exists.assert_called_once_with("test_key")
            mock_client.aclose.assert_called_once()


class TestGetDbPool:
    """Tests for get_db_pool() factory function."""

    @patch("libs.core.common.db_pool.AsyncConnectionAdapter")
    @patch("libs.core.common.db_pool.DATABASE_URL", "postgresql://localhost/testdb")
    def test_get_db_pool_success(self, mock_adapter_class):
        """Test get_db_pool() creates adapter when DATABASE_URL configured."""
        mock_adapter = Mock()
        mock_adapter_class.return_value = mock_adapter

        # Clear lru_cache to ensure fresh call
        get_db_pool.cache_clear()

        adapter = get_db_pool()

        # Verify adapter created with correct parameters
        mock_adapter_class.assert_called_once()
        call_args = mock_adapter_class.call_args
        assert call_args[0][0] == "postgresql://localhost/testdb"
        assert adapter is mock_adapter

    @patch("libs.core.common.db_pool.DATABASE_URL", "")
    def test_get_db_pool_missing_database_url(self, caplog):
        """Test get_db_pool() returns None when DATABASE_URL not set."""
        # Clear lru_cache to ensure fresh call
        get_db_pool.cache_clear()

        adapter = get_db_pool()

        assert adapter is None
        # Verify warning logged
        assert "db_pool_not_configured" in caplog.text

    @patch("libs.core.common.db_pool.AsyncConnectionAdapter")
    def test_get_db_pool_import_error(self, mock_adapter_class, caplog):
        """Test get_db_pool() handles ImportError gracefully."""
        mock_adapter_class.side_effect = ImportError("psycopg not installed")

        with patch.dict(os.environ, {"DATABASE_URL": "postgresql://localhost/testdb"}):
            # Clear lru_cache to ensure fresh call
            get_db_pool.cache_clear()

            adapter = get_db_pool()

            assert adapter is None
            # Verify exception logged
            assert "db_adapter_init_failed" in caplog.text

    @patch("libs.core.common.db_pool.AsyncConnectionAdapter")
    @patch("libs.core.common.db_pool.DATABASE_URL", "postgresql://localhost/testdb")
    def test_get_db_pool_singleton_behavior(self, mock_adapter_class):
        """Test get_db_pool() uses lru_cache singleton pattern."""
        mock_adapter = Mock()
        mock_adapter_class.return_value = mock_adapter

        # Clear lru_cache to start fresh
        get_db_pool.cache_clear()

        # Call twice
        adapter1 = get_db_pool()
        adapter2 = get_db_pool()

        # Verify singleton: AsyncConnectionAdapter created only once
        assert mock_adapter_class.call_count == 1
        assert adapter1 is adapter2


class TestGetRedisClient:
    """Tests for get_redis_client() factory function."""

    @patch("libs.core.common.db_pool.AsyncRedisAdapter")
    def test_get_redis_client_success(self, mock_adapter_class):
        """Test get_redis_client() creates adapter when REDIS_URL configured."""
        mock_adapter = Mock()
        mock_adapter_class.return_value = mock_adapter

        with patch.dict(
            os.environ,
            {
                "REDIS_URL": "redis://localhost:6379",
                "REDIS_STRATEGY_CACHE_DB": "5",
            },
        ):
            # Clear lru_cache to ensure fresh call
            get_redis_client.cache_clear()

            adapter = get_redis_client()

            # Verify adapter created with correct parameters
            mock_adapter_class.assert_called_once_with("redis://localhost:6379", db=5)
            assert adapter is mock_adapter

    @patch("libs.core.common.db_pool.AsyncRedisAdapter")
    def test_get_redis_client_default_cache_db(self, mock_adapter_class):
        """Test get_redis_client() uses default cache DB when not specified."""
        mock_adapter = Mock()
        mock_adapter_class.return_value = mock_adapter

        with patch.dict(os.environ, {"REDIS_URL": "redis://localhost:6379"}, clear=True):
            # Clear lru_cache to ensure fresh call
            get_redis_client.cache_clear()

            adapter = get_redis_client()

            # Verify adapter created with default db=3
            mock_adapter_class.assert_called_once_with("redis://localhost:6379", db=3)
            assert adapter is mock_adapter

    def test_get_redis_client_missing_redis_url(self, caplog):
        """Test get_redis_client() returns None when REDIS_URL not set."""
        with patch.dict(os.environ, {}, clear=True):
            # Clear lru_cache to ensure fresh call
            get_redis_client.cache_clear()

            adapter = get_redis_client()

            assert adapter is None
            # Verify warning logged
            assert "redis_client_not_configured" in caplog.text

    @patch("libs.core.common.db_pool.AsyncRedisAdapter")
    def test_get_redis_client_import_error(self, mock_adapter_class, caplog):
        """Test get_redis_client() handles ImportError gracefully."""
        mock_adapter_class.side_effect = ImportError("redis.asyncio not installed")

        with patch.dict(os.environ, {"REDIS_URL": "redis://localhost:6379"}):
            # Clear lru_cache to ensure fresh call
            get_redis_client.cache_clear()

            adapter = get_redis_client()

            assert adapter is None
            # Verify exception logged
            assert "redis_adapter_init_failed" in caplog.text

    @patch("libs.core.common.db_pool.AsyncRedisAdapter")
    def test_get_redis_client_value_error(self, mock_adapter_class, caplog):
        """Test get_redis_client() handles ValueError gracefully."""
        mock_adapter_class.side_effect = ValueError("Invalid db index")

        with patch.dict(os.environ, {"REDIS_URL": "redis://localhost:6379"}):
            # Clear lru_cache to ensure fresh call
            get_redis_client.cache_clear()

            adapter = get_redis_client()

            assert adapter is None
            # Verify exception logged
            assert "redis_adapter_init_failed" in caplog.text

    @patch("libs.core.common.db_pool.AsyncRedisAdapter")
    def test_get_redis_client_singleton_behavior(self, mock_adapter_class):
        """Test get_redis_client() uses lru_cache singleton pattern."""
        mock_adapter = Mock()
        mock_adapter_class.return_value = mock_adapter

        with patch.dict(os.environ, {"REDIS_URL": "redis://localhost:6379"}):
            # Clear lru_cache to start fresh
            get_redis_client.cache_clear()

            # Call twice
            adapter1 = get_redis_client()
            adapter2 = get_redis_client()

            # Verify singleton: AsyncRedisAdapter created only once
            assert mock_adapter_class.call_count == 1
            assert adapter1 is adapter2
