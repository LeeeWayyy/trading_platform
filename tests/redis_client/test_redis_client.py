"""
Unit tests for RedisClient connection manager.

Tests cover:
- Connection initialization
- GET/SET/DELETE operations
- Publish operations
- Health checks
- Retry logic
- Error handling
- Context manager usage
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from redis.exceptions import ConnectionError, TimeoutError, RedisError
from tenacity import RetryError

from libs.redis_client.client import RedisClient, RedisConnectionError


class TestRedisClientInitialization:
    """Tests for RedisClient initialization."""

    @patch('libs.redis_client.client.redis.Redis')
    @patch('libs.redis_client.client.ConnectionPool')
    def test_initialization_success(self, mock_pool_class, mock_redis_class):
        """Test successful Redis client initialization."""
        # Setup mocks
        mock_pool = Mock()
        mock_pool_class.return_value = mock_pool

        mock_redis = Mock()
        mock_redis.ping.return_value = True
        mock_redis_class.return_value = mock_redis

        # Initialize client
        client = RedisClient(
            host="localhost",
            port=6379,
            db=0,
            max_connections=10
        )

        # Verify connection pool created
        mock_pool_class.assert_called_once_with(
            host="localhost",
            port=6379,
            db=0,
            password=None,
            decode_responses=True,
            max_connections=10,
            socket_connect_timeout=5,
            socket_timeout=5
        )

        # Verify ping was called to test connection
        mock_redis.ping.assert_called_once()

    @patch('libs.redis_client.client.redis.Redis')
    @patch('libs.redis_client.client.ConnectionPool')
    def test_initialization_failure(self, mock_pool_class, mock_redis_class):
        """Test Redis client initialization failure."""
        # Setup mocks to raise ConnectionError
        mock_pool = Mock()
        mock_pool_class.return_value = mock_pool

        mock_redis = Mock()
        mock_redis.ping.side_effect = ConnectionError("Connection refused")
        mock_redis_class.return_value = mock_redis

        # Verify initialization raises RedisConnectionError
        with pytest.raises(RedisConnectionError, match="Cannot connect to Redis"):
            RedisClient(host="localhost", port=6379)


class TestRedisClientOperations:
    """Tests for Redis GET/SET/DELETE operations."""

    @pytest.fixture
    def mock_redis_client(self):
        """Create mock Redis client for testing."""
        with patch('libs.redis_client.client.redis.Redis') as mock_redis_class, \
             patch('libs.redis_client.client.ConnectionPool'):

            mock_redis = Mock()
            mock_redis.ping.return_value = True
            mock_redis_class.return_value = mock_redis

            client = RedisClient()
            client._client = mock_redis

            yield client, mock_redis

    def test_get_success(self, mock_redis_client):
        """Test successful GET operation."""
        client, mock_redis = mock_redis_client
        mock_redis.get.return_value = "test_value"

        result = client.get("test_key")

        assert result == "test_value"
        mock_redis.get.assert_called_once_with("test_key")

    def test_get_key_not_found(self, mock_redis_client):
        """Test GET operation when key doesn't exist."""
        client, mock_redis = mock_redis_client
        mock_redis.get.return_value = None

        result = client.get("nonexistent_key")

        assert result is None

    def test_get_with_retry(self, mock_redis_client):
        """Test GET operation with retry on transient error."""
        client, mock_redis = mock_redis_client

        # First call raises ConnectionError, second succeeds
        mock_redis.get.side_effect = [
            ConnectionError("Connection lost"),
            "test_value"
        ]

        result = client.get("test_key")

        assert result == "test_value"
        assert mock_redis.get.call_count == 2

    def test_set_success(self, mock_redis_client):
        """Test successful SET operation."""
        client, mock_redis = mock_redis_client

        client.set("test_key", "test_value")

        mock_redis.set.assert_called_once_with("test_key", "test_value")

    def test_set_with_ttl(self, mock_redis_client):
        """Test SET operation with TTL."""
        client, mock_redis = mock_redis_client

        client.set("test_key", "test_value", ttl=3600)

        mock_redis.setex.assert_called_once_with("test_key", 3600, "test_value")

    def test_delete_success(self, mock_redis_client):
        """Test successful DELETE operation."""
        client, mock_redis = mock_redis_client
        mock_redis.delete.return_value = 1

        result = client.delete("test_key")

        assert result == 1
        mock_redis.delete.assert_called_once_with("test_key")

    def test_delete_key_not_found(self, mock_redis_client):
        """Test DELETE operation when key doesn't exist."""
        client, mock_redis = mock_redis_client
        mock_redis.delete.return_value = 0

        result = client.delete("nonexistent_key")

        assert result == 0


class TestRedisClientPubSub:
    """Tests for Redis pub/sub operations."""

    @pytest.fixture
    def mock_redis_client(self):
        """Create mock Redis client for testing."""
        with patch('libs.redis_client.client.redis.Redis') as mock_redis_class, \
             patch('libs.redis_client.client.ConnectionPool'):

            mock_redis = Mock()
            mock_redis.ping.return_value = True
            mock_redis_class.return_value = mock_redis

            client = RedisClient()
            client._client = mock_redis

            yield client, mock_redis

    def test_publish_success(self, mock_redis_client):
        """Test successful PUBLISH operation."""
        client, mock_redis = mock_redis_client
        mock_redis.publish.return_value = 2  # 2 subscribers

        num_subscribers = client.publish("test_channel", "test_message")

        assert num_subscribers == 2
        mock_redis.publish.assert_called_once_with("test_channel", "test_message")

    def test_publish_no_subscribers(self, mock_redis_client):
        """Test PUBLISH operation with no subscribers."""
        client, mock_redis = mock_redis_client
        mock_redis.publish.return_value = 0

        num_subscribers = client.publish("test_channel", "test_message")

        assert num_subscribers == 0

    def test_pubsub_creation(self, mock_redis_client):
        """Test pubsub object creation."""
        client, mock_redis = mock_redis_client
        mock_pubsub = Mock()
        mock_redis.pubsub.return_value = mock_pubsub

        pubsub = client.pubsub()

        assert pubsub is mock_pubsub
        mock_redis.pubsub.assert_called_once()


class TestRedisClientHealthCheck:
    """Tests for Redis health check functionality."""

    @pytest.fixture
    def mock_redis_client(self):
        """Create mock Redis client for testing."""
        with patch('libs.redis_client.client.redis.Redis') as mock_redis_class, \
             patch('libs.redis_client.client.ConnectionPool'):

            mock_redis = Mock()
            mock_redis.ping.return_value = True
            mock_redis_class.return_value = mock_redis

            client = RedisClient()
            client._client = mock_redis

            yield client, mock_redis

    def test_health_check_healthy(self, mock_redis_client):
        """Test health check when Redis is healthy."""
        client, mock_redis = mock_redis_client
        mock_redis.ping.return_value = True

        result = client.health_check()

        assert result is True
        mock_redis.ping.assert_called()

    def test_health_check_unhealthy(self, mock_redis_client):
        """Test health check when Redis is unhealthy."""
        client, mock_redis = mock_redis_client
        mock_redis.ping.side_effect = ConnectionError("Connection refused")

        result = client.health_check()

        assert result is False

    def test_get_info_success(self, mock_redis_client):
        """Test getting Redis server info."""
        client, mock_redis = mock_redis_client
        mock_info = {
            "used_memory_human": "1.5M",
            "connected_clients": 5
        }
        mock_redis.info.return_value = mock_info

        result = client.get_info()

        assert result == mock_info
        mock_redis.info.assert_called_once()


class TestRedisClientContextManager:
    """Tests for context manager usage."""

    @patch('libs.redis_client.client.redis.Redis')
    @patch('libs.redis_client.client.ConnectionPool')
    def test_context_manager(self, mock_pool_class, mock_redis_class):
        """Test using RedisClient as context manager."""
        mock_pool = Mock()
        mock_pool_class.return_value = mock_pool

        mock_redis = Mock()
        mock_redis.ping.return_value = True
        mock_redis_class.return_value = mock_redis

        # Use client as context manager
        with RedisClient() as client:
            assert client is not None

        # Verify pool was disconnected on exit
        mock_pool.disconnect.assert_called_once()


class TestRedisClientErrorHandling:
    """Tests for error handling and edge cases."""

    @pytest.fixture
    def mock_redis_client(self):
        """Create mock Redis client for testing."""
        with patch('libs.redis_client.client.redis.Redis') as mock_redis_class, \
             patch('libs.redis_client.client.ConnectionPool'):

            mock_redis = Mock()
            mock_redis.ping.return_value = True
            mock_redis_class.return_value = mock_redis

            client = RedisClient()
            client._client = mock_redis

            yield client, mock_redis

    def test_get_persistent_error(self, mock_redis_client):
        """Test GET operation with persistent error (retries exhausted)."""
        client, mock_redis = mock_redis_client
        mock_redis.get.side_effect = ConnectionError("Persistent error")

        with pytest.raises(RetryError):
            client.get("test_key")

        # Verify retry logic attempted 3 times
        assert mock_redis.get.call_count == 3

    def test_set_persistent_error(self, mock_redis_client):
        """Test SET operation with persistent error."""
        client, mock_redis = mock_redis_client
        mock_redis.set.side_effect = TimeoutError("Timeout")

        with pytest.raises(RetryError):
            client.set("test_key", "value")

    def test_publish_persistent_error(self, mock_redis_client):
        """Test PUBLISH operation with persistent error."""
        client, mock_redis = mock_redis_client
        mock_redis.publish.side_effect = RedisError("Redis error")

        with pytest.raises(RedisError):
            client.publish("channel", "message")
