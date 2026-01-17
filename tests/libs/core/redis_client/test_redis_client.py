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

from unittest.mock import Mock, patch

import pytest
from redis.exceptions import ConnectionError, RedisError, TimeoutError
from tenacity import RetryError

from libs.core.redis_client.client import RedisClient, RedisConnectionError


class TestRedisClientInitialization:
    """Tests for RedisClient initialization."""

    @patch("libs.core.redis_client.client.redis.Redis")
    @patch("libs.core.redis_client.client.ConnectionPool")
    def test_initialization_success(self, mock_pool_class, mock_redis_class):
        """Test successful Redis client initialization."""
        # Setup mocks
        mock_pool = Mock()
        mock_pool_class.return_value = mock_pool

        mock_redis = Mock()
        mock_redis.ping.return_value = True
        mock_redis_class.return_value = mock_redis

        # Initialize client
        _client = RedisClient(host="localhost", port=6379, db=0, max_connections=10)

        # Verify connection pool created
        mock_pool_class.assert_called_once_with(
            host="localhost",
            port=6379,
            db=0,
            password=None,
            decode_responses=True,
            max_connections=10,
            socket_connect_timeout=5,
            socket_timeout=5,
        )

        # Verify ping was called to test connection
        mock_redis.ping.assert_called_once()

    @patch("libs.core.redis_client.client.redis.Redis")
    @patch("libs.core.redis_client.client.ConnectionPool")
    def test_initialization_failure(self, mock_pool_class, mock_redis_class):
        """Test Redis client initialization failure."""
        # Setup mocks to raise ConnectionError
        mock_pool = Mock()
        mock_pool_class.return_value = mock_pool

        mock_redis = Mock()
        mock_redis.ping.side_effect = ConnectionError("Connection refused")
        mock_redis_class.return_value = mock_redis

        # Verify initialization raises RedisConnectionError
        with pytest.raises(RedisConnectionError):
            RedisClient(host="localhost", port=6379)


class TestRedisClientOperations:
    """Tests for Redis GET/SET/DELETE operations."""

    @pytest.fixture()
    def mock_redis_client(self):
        """Create mock Redis client for testing."""
        with (
            patch("libs.core.redis_client.client.redis.Redis") as mock_redis_class,
            patch("libs.core.redis_client.client.ConnectionPool"),
        ):
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
        mock_redis.get.side_effect = [ConnectionError("Connection lost"), "test_value"]

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

    @pytest.fixture()
    def mock_redis_client(self):
        """Create mock Redis client for testing."""
        with (
            patch("libs.core.redis_client.client.redis.Redis") as mock_redis_class,
            patch("libs.core.redis_client.client.ConnectionPool"),
        ):
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

    @pytest.fixture()
    def mock_redis_client(self):
        """Create mock Redis client for testing."""
        with (
            patch("libs.core.redis_client.client.redis.Redis") as mock_redis_class,
            patch("libs.core.redis_client.client.ConnectionPool"),
        ):
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
        mock_info = {"used_memory_human": "1.5M", "connected_clients": 5}
        mock_redis.info.return_value = mock_info

        result = client.get_info()

        assert result == mock_info
        mock_redis.info.assert_called_once()


class TestRedisClientContextManager:
    """Tests for context manager usage."""

    @patch("libs.core.redis_client.client.redis.Redis")
    @patch("libs.core.redis_client.client.ConnectionPool")
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

    @pytest.fixture()
    def mock_redis_client(self):
        """Create mock Redis client for testing."""
        with (
            patch("libs.core.redis_client.client.redis.Redis") as mock_redis_class,
            patch("libs.core.redis_client.client.ConnectionPool"),
        ):
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


class TestRedisClientListOperations:
    """
    Tests for Redis list operations (rpush, ltrim, lrange).

    These operations are critical for kill-switch history tracking.
    Without these methods, kill-switch engage/disengage operations crash.
    """

    @pytest.fixture()
    def mock_redis_client(self):
        """Create mock Redis client for testing."""
        with (
            patch("libs.core.redis_client.client.redis.Redis") as mock_redis_class,
            patch("libs.core.redis_client.client.ConnectionPool"),
        ):
            mock_redis = Mock()
            mock_redis.ping.return_value = True
            mock_redis_class.return_value = mock_redis

            client = RedisClient()
            client._client = mock_redis

            yield client, mock_redis

    def test_rpush_single_value(self, mock_redis_client):
        """Test RPUSH operation with single value."""
        client, mock_redis = mock_redis_client
        mock_redis.rpush.return_value = 1  # List length after push

        result = client.rpush("test_list", "value1")

        assert result == 1
        mock_redis.rpush.assert_called_once_with("test_list", "value1")

    def test_rpush_multiple_values(self, mock_redis_client):
        """Test RPUSH operation with multiple values."""
        client, mock_redis = mock_redis_client
        mock_redis.rpush.return_value = 3  # List length after push

        result = client.rpush("test_list", "value1", "value2", "value3")

        assert result == 3
        mock_redis.rpush.assert_called_once_with("test_list", "value1", "value2", "value3")

    def test_rpush_error_handling(self, mock_redis_client):
        """Test RPUSH operation error handling."""
        client, mock_redis = mock_redis_client
        mock_redis.rpush.side_effect = RedisError("RPUSH failed")

        with pytest.raises(RedisError, match="RPUSH failed"):
            client.rpush("test_list", "value1")

    def test_ltrim_success(self, mock_redis_client):
        """Test LTRIM operation success."""
        client, mock_redis = mock_redis_client
        mock_redis.ltrim.return_value = True

        result = client.ltrim("test_list", 0, 99)

        assert result is True
        mock_redis.ltrim.assert_called_once_with("test_list", 0, 99)

    def test_ltrim_keep_last_n(self, mock_redis_client):
        """Test LTRIM operation to keep last N items."""
        client, mock_redis = mock_redis_client
        mock_redis.ltrim.return_value = True

        # Keep last 100 items
        result = client.ltrim("history:kill-switch", -100, -1)

        assert result is True
        mock_redis.ltrim.assert_called_once_with("history:kill-switch", -100, -1)

    def test_ltrim_error_handling(self, mock_redis_client):
        """Test LTRIM operation error handling."""
        client, mock_redis = mock_redis_client
        mock_redis.ltrim.side_effect = RedisError("LTRIM failed")

        with pytest.raises(RedisError, match="LTRIM failed"):
            client.ltrim("test_list", 0, 99)

    def test_lrange_success(self, mock_redis_client):
        """Test LRANGE operation success."""
        client, mock_redis = mock_redis_client
        mock_redis.lrange.return_value = [b"value1", b"value2", b"value3"]

        result = client.lrange("test_list", 0, -1)

        assert result == [b"value1", b"value2", b"value3"]
        mock_redis.lrange.assert_called_once_with("test_list", 0, -1)

    def test_lrange_empty_list(self, mock_redis_client):
        """Test LRANGE operation on empty list."""
        client, mock_redis = mock_redis_client
        mock_redis.lrange.return_value = []

        result = client.lrange("empty_list", 0, -1)

        assert result == []

    def test_lrange_get_last_n(self, mock_redis_client):
        """Test LRANGE operation to get last N items."""
        client, mock_redis = mock_redis_client
        mock_redis.lrange.return_value = [
            b'{"operator": "ops_team", "action": "engage"}',
            b'{"operator": "ops_team", "action": "disengage"}',
        ]

        # Get last 10 items from kill-switch history
        result = client.lrange("history:kill-switch", -10, -1)

        assert len(result) == 2
        mock_redis.lrange.assert_called_once_with("history:kill-switch", -10, -1)

    def test_lrange_error_handling(self, mock_redis_client):
        """Test LRANGE operation error handling."""
        client, mock_redis = mock_redis_client
        mock_redis.lrange.side_effect = RedisError("LRANGE failed")

        with pytest.raises(RedisError, match="LRANGE failed"):
            client.lrange("test_list", 0, -1)

    def test_kill_switch_history_workflow(self, mock_redis_client):
        """
        Test complete kill-switch history workflow using list operations.

        This simulates how KillSwitch uses these operations:
        1. RPUSH to append history event
        2. LTRIM to keep last 100 events
        3. LRANGE to retrieve recent history
        """
        client, mock_redis = mock_redis_client

        # Step 1: Append history event
        mock_redis.rpush.return_value = 1
        length = client.rpush("history:kill-switch", '{"action": "engage", "operator": "ops"}')
        assert length == 1

        # Step 2: Trim to keep last 100 events
        mock_redis.ltrim.return_value = True
        trimmed = client.ltrim("history:kill-switch", -100, -1)
        assert trimmed is True

        # Step 3: Get recent history
        mock_redis.lrange.return_value = [b'{"action": "engage", "operator": "ops"}']
        history = client.lrange("history:kill-switch", -10, -1)
        assert len(history) == 1

        # Verify all operations were called correctly
        mock_redis.rpush.assert_called_once()
        mock_redis.ltrim.assert_called_once()
        mock_redis.lrange.assert_called_once()
"""
P0 Coverage Tests for RedisClient - Additional branch coverage to reach 95%+ target.

Missing branches from coverage report (69% → 95%):
- Line 196: mget with empty keys list (early return)
- Lines 201-203: mget RedisError exception handling
- Lines 219-222: lock method (not tested yet)
- Lines 280-285: set_if_not_exists RedisError exception handling
- Line 296: setnx method (alias, not tested yet)
- Lines 325-327: delete RedisError exception handling
- Lines 413-415: sadd RedisError exception handling
- Lines 430-432: smembers RedisError exception handling
- Lines 448-458: sscan_iter RedisError exception handling
- Lines 478-480: zadd RedisError exception handling
- Lines 499-501: zcard RedisError exception handling
- Lines 523-525: zremrangebyrank RedisError exception handling
- Lines 564-566: get_info RedisError exception handling
- Lines 709-711: eval RedisError exception handling
- Lines 738-743: incr RedisError exception handling
- Lines 768-773: expire RedisError exception handling
- Lines 800-805: zrevrange RedisError exception handling
- Lines 827-832: zrem RedisError exception handling
- Line 856: __repr__ method (not tested yet)
"""

from unittest.mock import patch

import pytest


class TestRedisClientMgetEdgeCases:
    """Tests for mget() method edge cases."""

    def test_mget_empty_keys_list(self):
        """Test mget() with empty keys list returns empty list (early return)."""
        with patch("libs.core.redis_client.client.redis.Redis") as mock_redis_class:
            mock_client = Mock()
            mock_client.ping.return_value = True
            mock_redis_class.return_value = mock_client

            client = RedisClient()
            result = client.mget([])

            # Verify empty list returned without calling Redis
            assert result == []
            mock_client.mget.assert_not_called()

    def test_mget_redis_error(self):
        """Test mget() handles RedisError exception."""
        with patch("libs.core.redis_client.client.redis.Redis") as mock_redis_class:
            mock_client = Mock()
            mock_client.ping.return_value = True
            mock_client.mget.side_effect = RedisError("Connection lost")
            mock_redis_class.return_value = mock_client

            client = RedisClient()

            # Attempt mget should raise RedisError after retries
            with pytest.raises(RedisError, match="Connection lost"):
                client.mget(["key1", "key2"])


class TestRedisClientLock:
    """Tests for lock() distributed locking method."""

    def test_lock_creates_redis_lock(self):
        """Test lock() returns a Redis distributed lock."""
        with patch("libs.core.redis_client.client.redis.Redis") as mock_redis_class:
            mock_client = Mock()
            mock_client.ping.return_value = True
            mock_lock = Mock()
            mock_client.lock.return_value = mock_lock
            mock_redis_class.return_value = mock_client

            client = RedisClient()
            lock = client.lock("mylock", timeout=30, blocking_timeout=10.0)

            # Verify lock was created with correct parameters
            assert lock == mock_lock
            mock_client.lock.assert_called_once_with(
                "mylock", timeout=30, blocking_timeout=10.0
            )

    def test_lock_without_blocking_timeout(self):
        """Test lock() with blocking_timeout=None (wait forever)."""
        with patch("libs.core.redis_client.client.redis.Redis") as mock_redis_class:
            mock_client = Mock()
            mock_client.ping.return_value = True
            mock_lock = Mock()
            mock_client.lock.return_value = mock_lock
            mock_redis_class.return_value = mock_client

            client = RedisClient()
            lock = client.lock("mylock", timeout=60, blocking_timeout=None)

            # Verify lock was created with None blocking timeout
            assert lock == mock_lock
            mock_client.lock.assert_called_once_with(
                "mylock", timeout=60, blocking_timeout=None
            )


class TestRedisClientSetIfNotExists:
    """Tests for set_if_not_exists() atomic operation."""

    def test_set_if_not_exists_redis_error(self):
        """Test set_if_not_exists() handles RedisError exception."""
        with patch("libs.core.redis_client.client.redis.Redis") as mock_redis_class:
            mock_client = Mock()
            mock_client.ping.return_value = True
            mock_client.set.side_effect = RedisError("SETNX failed")
            mock_redis_class.return_value = mock_client

            client = RedisClient()

            # Attempt set_if_not_exists should raise RedisError
            with pytest.raises(RedisError, match="SETNX failed"):
                client.set_if_not_exists("key", "value", ex=60)


class TestRedisClientSetnx:
    """Tests for setnx() backward-compatible alias."""

    def test_setnx_calls_set_if_not_exists(self):
        """Test setnx() is an alias for set_if_not_exists()."""
        with patch("libs.core.redis_client.client.redis.Redis") as mock_redis_class:
            mock_client = Mock()
            mock_client.ping.return_value = True
            mock_client.set.return_value = True  # Key was set
            mock_redis_class.return_value = mock_client

            client = RedisClient()
            result = client.setnx("key", "value", ex=30)

            # Verify setnx delegates to set_if_not_exists
            assert result is True
            mock_client.set.assert_called_once_with("key", "value", nx=True, ex=30)


class TestRedisClientDeleteError:
    """Tests for delete() exception handling."""

    def test_delete_redis_error(self):
        """Test delete() handles RedisError exception."""
        with patch("libs.core.redis_client.client.redis.Redis") as mock_redis_class:
            mock_client = Mock()
            mock_client.ping.return_value = True
            mock_client.delete.side_effect = RedisError("DELETE failed")
            mock_redis_class.return_value = mock_client

            client = RedisClient()

            # Attempt delete should raise RedisError
            with pytest.raises(RedisError, match="DELETE failed"):
                client.delete("key1", "key2")


class TestRedisClientSetOperationsErrors:
    """Tests for set operations (sadd, smembers, sscan_iter) exception handling."""

    def test_sadd_redis_error(self):
        """Test sadd() handles RedisError exception."""
        with patch("libs.core.redis_client.client.redis.Redis") as mock_redis_class:
            mock_client = Mock()
            mock_client.ping.return_value = True
            mock_client.sadd.side_effect = RedisError("SADD failed")
            mock_redis_class.return_value = mock_client

            client = RedisClient()

            # Attempt sadd should raise RedisError
            with pytest.raises(RedisError, match="SADD failed"):
                client.sadd("myset", "member1", "member2")

    def test_smembers_redis_error(self):
        """Test smembers() handles RedisError exception."""
        with patch("libs.core.redis_client.client.redis.Redis") as mock_redis_class:
            mock_client = Mock()
            mock_client.ping.return_value = True
            mock_client.smembers.side_effect = RedisError("SMEMBERS failed")
            mock_redis_class.return_value = mock_client

            client = RedisClient()

            # Attempt smembers should raise RedisError
            with pytest.raises(RedisError, match="SMEMBERS failed"):
                client.smembers("myset")

    def test_sscan_iter_redis_error(self):
        """Test sscan_iter() handles RedisError exception."""
        with patch("libs.core.redis_client.client.redis.Redis") as mock_redis_class:
            mock_client = Mock()
            mock_client.ping.return_value = True
            mock_client.sscan_iter.side_effect = RedisError("SSCAN failed")
            mock_redis_class.return_value = mock_client

            client = RedisClient()

            # Attempt to iterate should raise RedisError
            with pytest.raises(RedisError, match="SSCAN failed"):
                list(client.sscan_iter("myset", count=50))


class TestRedisClientSortedSetOperationsErrors:
    """Tests for sorted set operations (zadd, zcard, etc.) exception handling."""

    def test_zadd_redis_error(self):
        """Test zadd() handles RedisError exception."""
        with patch("libs.core.redis_client.client.redis.Redis") as mock_redis_class:
            mock_client = Mock()
            mock_client.ping.return_value = True
            mock_client.zadd.side_effect = RedisError("ZADD failed")
            mock_redis_class.return_value = mock_client

            client = RedisClient()

            # Attempt zadd should raise RedisError
            with pytest.raises(RedisError, match="ZADD failed"):
                client.zadd("myzset", {"member1": 1.0, "member2": 2.0})

    def test_zcard_redis_error(self):
        """Test zcard() handles RedisError exception."""
        with patch("libs.core.redis_client.client.redis.Redis") as mock_redis_class:
            mock_client = Mock()
            mock_client.ping.return_value = True
            mock_client.zcard.side_effect = RedisError("ZCARD failed")
            mock_redis_class.return_value = mock_client

            client = RedisClient()

            # Attempt zcard should raise RedisError
            with pytest.raises(RedisError, match="ZCARD failed"):
                client.zcard("myzset")

    def test_zremrangebyrank_redis_error(self):
        """Test zremrangebyrank() handles RedisError exception."""
        with patch("libs.core.redis_client.client.redis.Redis") as mock_redis_class:
            mock_client = Mock()
            mock_client.ping.return_value = True
            mock_client.zremrangebyrank.side_effect = RedisError("ZREMRANGEBYRANK failed")
            mock_redis_class.return_value = mock_client

            client = RedisClient()

            # Attempt zremrangebyrank should raise RedisError
            with pytest.raises(RedisError, match="ZREMRANGEBYRANK failed"):
                client.zremrangebyrank("myzset", 0, 10)

    def test_zrevrange_redis_error(self):
        """Test zrevrange() handles RedisError exception."""
        with patch("libs.core.redis_client.client.redis.Redis") as mock_redis_class:
            mock_client = Mock()
            mock_client.ping.return_value = True
            mock_client.zrevrange.side_effect = RedisError("ZREVRANGE failed")
            mock_redis_class.return_value = mock_client

            client = RedisClient()

            # Attempt zrevrange should raise RedisError
            with pytest.raises(RedisError, match="ZREVRANGE failed"):
                client.zrevrange("myzset", 0, 10, withscores=True)

    def test_zrem_redis_error(self):
        """Test zrem() handles RedisError exception."""
        with patch("libs.core.redis_client.client.redis.Redis") as mock_redis_class:
            mock_client = Mock()
            mock_client.ping.return_value = True
            mock_client.zrem.side_effect = RedisError("ZREM failed")
            mock_redis_class.return_value = mock_client

            client = RedisClient()

            # Attempt zrem should raise RedisError
            with pytest.raises(RedisError, match="ZREM failed"):
                client.zrem("myzset", "member1", "member2")


class TestRedisClientServerOperationsErrors:
    """Tests for server operations (get_info, eval, etc.) exception handling."""

    def test_get_info_redis_error(self):
        """Test get_info() handles RedisError exception."""
        with patch("libs.core.redis_client.client.redis.Redis") as mock_redis_class:
            mock_client = Mock()
            mock_client.ping.return_value = True
            mock_client.info.side_effect = RedisError("INFO failed")
            mock_redis_class.return_value = mock_client

            client = RedisClient()

            # Attempt get_info should raise RedisError
            with pytest.raises(RedisError, match="INFO failed"):
                client.get_info()

    def test_eval_redis_error(self):
        """Test eval() handles RedisError exception."""
        with patch("libs.core.redis_client.client.redis.Redis") as mock_redis_class:
            mock_client = Mock()
            mock_client.ping.return_value = True
            mock_client.eval.side_effect = RedisError("EVAL failed")
            mock_redis_class.return_value = mock_client

            client = RedisClient()

            # Attempt eval should raise RedisError
            script = "return redis.call('GET', KEYS[1])"
            with pytest.raises(RedisError, match="EVAL failed"):
                client.eval(script, 1, "mykey")


class TestRedisClientStringOperationsErrors:
    """Tests for string operations (incr, expire) exception handling."""

    def test_incr_redis_error(self):
        """Test incr() handles RedisError exception."""
        with patch("libs.core.redis_client.client.redis.Redis") as mock_redis_class:
            mock_client = Mock()
            mock_client.ping.return_value = True
            mock_client.incr.side_effect = RedisError("INCR failed")
            mock_redis_class.return_value = mock_client

            client = RedisClient()

            # Attempt incr should raise RedisError
            with pytest.raises(RedisError, match="INCR failed"):
                client.incr("counter")

    def test_expire_redis_error(self):
        """Test expire() handles RedisError exception."""
        with patch("libs.core.redis_client.client.redis.Redis") as mock_redis_class:
            mock_client = Mock()
            mock_client.ping.return_value = True
            mock_client.expire.side_effect = RedisError("EXPIRE failed")
            mock_redis_class.return_value = mock_client

            client = RedisClient()

            # Attempt expire should raise RedisError
            with pytest.raises(RedisError, match="EXPIRE failed"):
                client.expire("mykey", 60)


class TestRedisClientRepr:
    """Tests for __repr__() method."""

    def test_repr_includes_connection_info(self):
        """Test __repr__() returns formatted string with connection details."""
        with patch("libs.core.redis_client.client.redis.Redis") as mock_redis_class:
            mock_client = Mock()
            mock_client.ping.return_value = True
            mock_redis_class.return_value = mock_client

            client = RedisClient(host="prod-redis", port=6380, db=2)
            repr_str = repr(client)

            # Verify repr includes connection details
            assert "RedisClient" in repr_str
            assert "prod-redis" in repr_str
            assert "6380" in repr_str
            assert "db=2" in repr_str
