"""
Unit tests for FeatureCache.

Tests cover:
- Feature caching (set/get)
- Cache hits and misses
- TTL expiration
- Cache invalidation
- JSON serialization
- Error handling
- Statistics
"""

import json
from unittest.mock import Mock

import pytest
from redis.exceptions import RedisError

from libs.redis_client.feature_cache import FeatureCache


class TestFeatureCacheInitialization:
    """Tests for FeatureCache initialization."""

    def test_initialization_default_params(self):
        """Test initialization with default parameters."""
        mock_redis = Mock()
        cache = FeatureCache(mock_redis)

        assert cache.redis is mock_redis
        assert cache.ttl == 3600  # Default 1 hour
        assert cache.prefix == "features"

    def test_initialization_custom_params(self):
        """Test initialization with custom parameters."""
        mock_redis = Mock()
        cache = FeatureCache(mock_redis, ttl=7200, prefix="custom_features")

        assert cache.ttl == 7200
        assert cache.prefix == "custom_features"


class TestFeatureCacheKeyGeneration:
    """Tests for Redis key generation."""

    def test_make_key_format(self):
        """Test key format is correct."""
        mock_redis = Mock()
        cache = FeatureCache(mock_redis)

        key = cache._make_key("AAPL", "2025-01-17")

        assert key == "features:AAPL:2025-01-17"

    def test_make_key_with_custom_prefix(self):
        """Test key generation with custom prefix."""
        mock_redis = Mock()
        cache = FeatureCache(mock_redis, prefix="test")

        key = cache._make_key("MSFT", "2025-01-18")

        assert key == "test:MSFT:2025-01-18"


class TestFeatureCacheGet:
    """Tests for cache GET operations."""

    @pytest.fixture
    def mock_cache(self):
        """Create mock feature cache."""
        mock_redis = Mock()
        cache = FeatureCache(mock_redis, ttl=3600)
        return cache, mock_redis

    def test_get_cache_hit(self, mock_cache):
        """Test cache GET when key exists (cache HIT)."""
        cache, mock_redis = mock_cache

        # Mock Redis returns JSON string
        features_dict = {"feature_1": 0.5, "feature_2": 0.3}
        mock_redis.get.return_value = json.dumps(features_dict)

        result = cache.get("AAPL", "2025-01-17")

        assert result == features_dict
        mock_redis.get.assert_called_once_with("features:AAPL:2025-01-17")

    def test_get_cache_miss(self, mock_cache):
        """Test cache GET when key doesn't exist (cache MISS)."""
        cache, mock_redis = mock_cache
        mock_redis.get.return_value = None

        result = cache.get("AAPL", "2025-01-17")

        assert result is None
        mock_redis.get.assert_called_once_with("features:AAPL:2025-01-17")

    def test_get_invalid_json(self, mock_cache):
        """Test cache GET with invalid JSON data."""
        cache, mock_redis = mock_cache
        mock_redis.get.return_value = "invalid json {"

        result = cache.get("AAPL", "2025-01-17")

        # Should return None and invalidate corrupted data
        assert result is None
        mock_redis.delete.assert_called_once_with("features:AAPL:2025-01-17")

    def test_get_redis_error(self, mock_cache):
        """Test cache GET with Redis error."""
        cache, mock_redis = mock_cache
        mock_redis.get.side_effect = RedisError("Connection lost")

        result = cache.get("AAPL", "2025-01-17")

        # Should return None (graceful degradation)
        assert result is None

    def test_get_complex_features(self, mock_cache):
        """Test cache GET with complex feature dictionary."""
        cache, mock_redis = mock_cache

        # Complex features with nested structures
        features_dict = {
            "feature_1": 0.123456,
            "feature_2": -0.987654,
            "feature_3": 0.0,
            "metadata": {"count": 100},
        }
        mock_redis.get.return_value = json.dumps(features_dict)

        result = cache.get("GOOGL", "2025-01-17")

        assert result == features_dict


class TestFeatureCacheSet:
    """Tests for cache SET operations."""

    @pytest.fixture
    def mock_cache(self):
        """Create mock feature cache."""
        mock_redis = Mock()
        cache = FeatureCache(mock_redis, ttl=3600)
        return cache, mock_redis

    def test_set_success(self, mock_cache):
        """Test successful cache SET operation."""
        cache, mock_redis = mock_cache

        features = {"feature_1": 0.5, "feature_2": 0.3}
        result = cache.set("AAPL", "2025-01-17", features)

        assert result is True
        mock_redis.set.assert_called_once_with(
            "features:AAPL:2025-01-17", json.dumps(features), ttl=3600
        )

    def test_set_with_custom_ttl(self, mock_cache):
        """Test cache SET with custom TTL."""
        cache, _ = mock_cache
        mock_redis = Mock()
        cache.redis = mock_redis
        cache.ttl = 7200  # 2 hours

        features = {"feature_1": 0.5}
        result = cache.set("MSFT", "2025-01-17", features)

        assert result is True
        mock_redis.set.assert_called_once()
        call_args = mock_redis.set.call_args
        assert call_args[1]["ttl"] == 7200

    def test_set_empty_features(self, mock_cache):
        """Test cache SET with empty feature dictionary."""
        cache, mock_redis = mock_cache

        features = {}
        result = cache.set("AAPL", "2025-01-17", features)

        assert result is True
        mock_redis.set.assert_called_once()

    def test_set_non_serializable(self, mock_cache):
        """Test cache SET with non-JSON-serializable features."""
        cache, mock_redis = mock_cache

        # Object type is not JSON serializable
        features = {"function": lambda x: x}

        result = cache.set("AAPL", "2025-01-17", features)

        # Should return False on serialization error
        assert result is False
        mock_redis.set.assert_not_called()

    def test_set_redis_error(self, mock_cache):
        """Test cache SET with Redis error."""
        cache, mock_redis = mock_cache
        mock_redis.set.side_effect = RedisError("Connection lost")

        features = {"feature_1": 0.5}
        result = cache.set("AAPL", "2025-01-17", features)

        # Should return False (graceful degradation)
        assert result is False


class TestFeatureCacheInvalidation:
    """Tests for cache invalidation."""

    @pytest.fixture
    def mock_cache(self):
        """Create mock feature cache."""
        mock_redis = Mock()
        cache = FeatureCache(mock_redis, ttl=3600)
        return cache, mock_redis

    def test_invalidate_existing_key(self, mock_cache):
        """Test invalidating existing cache key."""
        cache, mock_redis = mock_cache
        mock_redis.delete.return_value = 1  # Key was deleted

        result = cache.invalidate("AAPL", "2025-01-17")

        assert result is True
        mock_redis.delete.assert_called_once_with("features:AAPL:2025-01-17")

    def test_invalidate_nonexistent_key(self, mock_cache):
        """Test invalidating nonexistent cache key."""
        cache, mock_redis = mock_cache
        mock_redis.delete.return_value = 0  # Key didn't exist

        result = cache.invalidate("AAPL", "2025-01-17")

        assert result is False

    def test_invalidate_redis_error(self, mock_cache):
        """Test invalidation with Redis error."""
        cache, mock_redis = mock_cache
        mock_redis.delete.side_effect = RedisError("Connection lost")

        result = cache.invalidate("AAPL", "2025-01-17")

        # Should return False (graceful degradation)
        assert result is False


class TestFeatureCacheStatistics:
    """Tests for cache statistics."""

    @pytest.fixture
    def mock_cache(self):
        """Create mock feature cache."""
        mock_redis = Mock()
        cache = FeatureCache(mock_redis, ttl=3600)
        return cache, mock_redis

    def test_get_stats_success(self, mock_cache):
        """Test getting cache statistics."""
        cache, mock_redis = mock_cache

        mock_info = {
            "keyspace_hits": 1000,
            "keyspace_misses": 200,
            "used_memory_human": "1.5M",
            "connected_clients": 5,
        }
        mock_redis.get_info.return_value = mock_info

        stats = cache.get_stats()

        assert stats["keyspace_hits"] == 1000
        assert stats["keyspace_misses"] == 200
        assert stats["used_memory"] == "1.5M"
        assert stats["connected_clients"] == 5

    def test_get_stats_redis_error(self, mock_cache):
        """Test getting stats with Redis error."""
        cache, mock_redis = mock_cache
        mock_redis.get_info.side_effect = RedisError("Connection lost")

        stats = cache.get_stats()

        # Should return empty dict on error
        assert stats == {}


class TestFeatureCacheEndToEnd:
    """End-to-end tests for typical cache usage patterns."""

    @pytest.fixture
    def mock_cache(self):
        """Create mock feature cache."""
        mock_redis = Mock()
        cache = FeatureCache(mock_redis, ttl=3600)
        return cache, mock_redis

    def test_cache_miss_then_set_then_hit(self, mock_cache):
        """Test typical pattern: MISS -> SET -> HIT."""
        cache, mock_redis = mock_cache

        # Simulate cache MISS
        mock_redis.get.return_value = None
        result1 = cache.get("AAPL", "2025-01-17")
        assert result1 is None

        # Set features in cache
        features = {"feature_1": 0.5, "feature_2": 0.3}
        success = cache.set("AAPL", "2025-01-17", features)
        assert success is True

        # Simulate cache HIT
        mock_redis.get.return_value = json.dumps(features)
        result2 = cache.get("AAPL", "2025-01-17")
        assert result2 == features

    def test_set_then_invalidate_then_miss(self, mock_cache):
        """Test pattern: SET -> INVALIDATE -> MISS."""
        cache, mock_redis = mock_cache

        # Set features
        features = {"feature_1": 0.5}
        cache.set("AAPL", "2025-01-17", features)

        # Invalidate
        mock_redis.delete.return_value = 1
        success = cache.invalidate("AAPL", "2025-01-17")
        assert success is True

        # Verify cache MISS
        mock_redis.get.return_value = None
        result = cache.get("AAPL", "2025-01-17")
        assert result is None

    def test_multiple_symbols_same_date(self, mock_cache):
        """Test caching features for multiple symbols on same date."""
        cache, mock_redis = mock_cache

        # Cache features for multiple symbols
        symbols = ["AAPL", "MSFT", "GOOGL"]
        features_map = {}

        for symbol in symbols:
            features = {f"feature_{symbol}": 0.5}
            cache.set(symbol, "2025-01-17", features)
            features_map[symbol] = features

        # Verify all symbols can be retrieved
        for symbol in symbols:
            mock_redis.get.return_value = json.dumps(features_map[symbol])
            result = cache.get(symbol, "2025-01-17")
            assert result == features_map[symbol]

    def test_same_symbol_multiple_dates(self, mock_cache):
        """Test caching features for same symbol across multiple dates."""
        cache, mock_redis = mock_cache

        # Cache features for multiple dates
        dates = ["2025-01-15", "2025-01-16", "2025-01-17"]
        features_map = {}

        for date in dates:
            features = {f"feature_{date}": 0.5}
            cache.set("AAPL", date, features)
            features_map[date] = features

        # Verify all dates can be retrieved
        for date in dates:
            mock_redis.get.return_value = json.dumps(features_map[date])
            result = cache.get("AAPL", date)
            assert result == features_map[date]
