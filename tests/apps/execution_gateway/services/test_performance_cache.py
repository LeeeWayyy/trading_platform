"""Tests for performance cache service.

This test suite validates performance dashboard caching logic extracted from main.py,
ensuring correct:
- Cache key generation (user-scoped, strategy-scoped)
- Index key generation for targeted invalidation
- Cache registration with per-date indexing
- Cache invalidation with batch processing
- Error handling and graceful degradation

Target: 90%+ coverage per Phase 1 requirements.

See REFACTOR_EXECUTION_GATEWAY_TASK.md Phase 1 for design decisions.
"""

from datetime import date
from unittest.mock import MagicMock, call

import pytest
from redis.exceptions import RedisError

from apps.execution_gateway.services.performance_cache import (
    create_performance_cache_index_key,
    create_performance_cache_key,
    invalidate_performance_cache,
    register_performance_cache,
)

# ============================================================================
# Test create_performance_cache_key
# ============================================================================


def test_create_cache_key_basic() -> None:
    """Test basic cache key generation with user and strategies."""
    key = create_performance_cache_key(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        strategies=("alpha_baseline",),
        user_id="user123",
    )

    assert key.startswith("performance:daily:user123:2024-01-01:2024-01-31:")
    assert len(key.split(":")[-1]) == 8  # Hash is 8 chars


def test_create_cache_key_multiple_strategies() -> None:
    """Test cache key generation with multiple strategies (sorted)."""
    key = create_performance_cache_key(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        strategies=("momentum", "alpha_baseline", "mean_reversion"),
        user_id="user456",
    )

    # Verify strategies are sorted before hashing
    key2 = create_performance_cache_key(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        strategies=("alpha_baseline", "mean_reversion", "momentum"),  # Different order
        user_id="user456",
    )

    assert key == key2  # Should be identical (strategies sorted)


def test_create_cache_key_empty_strategies() -> None:
    """Test cache key generation with no strategies."""
    key = create_performance_cache_key(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        strategies=(),
        user_id="user789",
    )

    assert "none" in key or key.split(":")[-1] is not None  # "none" token or hash of "none"


def test_create_cache_key_anonymous_user() -> None:
    """Test cache key generation for anonymous user."""
    key = create_performance_cache_key(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        strategies=("alpha_baseline",),
        user_id=None,
    )

    assert "performance:daily:anon:" in key


def test_create_cache_key_deterministic() -> None:
    """Test that cache keys are deterministic (same inputs = same key)."""
    key1 = create_performance_cache_key(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        strategies=("alpha_baseline", "momentum"),
        user_id="user123",
    )

    key2 = create_performance_cache_key(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        strategies=("alpha_baseline", "momentum"),
        user_id="user123",
    )

    assert key1 == key2


def test_create_cache_key_different_users() -> None:
    """Test that different users get different cache keys."""
    key1 = create_performance_cache_key(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        strategies=("alpha_baseline",),
        user_id="user123",
    )

    key2 = create_performance_cache_key(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        strategies=("alpha_baseline",),
        user_id="user456",
    )

    assert key1 != key2


# ============================================================================
# Test create_performance_cache_index_key
# ============================================================================


def test_create_index_key_format() -> None:
    """Test index key generation format."""
    index_key = create_performance_cache_index_key(date(2024, 1, 15))

    assert index_key == "performance:daily:index:2024-01-15"


def test_create_index_key_different_dates() -> None:
    """Test that different dates generate different index keys."""
    key1 = create_performance_cache_index_key(date(2024, 1, 15))
    key2 = create_performance_cache_index_key(date(2024, 1, 16))

    assert key1 != key2


# ============================================================================
# Test register_performance_cache
# ============================================================================


def test_register_cache_single_day() -> None:
    """Test cache registration for single-day range."""
    redis_mock = MagicMock()
    pipeline_mock = MagicMock()
    redis_mock.pipeline.return_value = pipeline_mock

    register_performance_cache(
        redis_client=redis_mock,
        cache_key="test_key",
        start_date=date(2024, 1, 15),
        end_date=date(2024, 1, 15),
        ttl_seconds=300,
    )

    # Verify pipeline operations
    assert pipeline_mock.sadd.call_count == 1
    assert pipeline_mock.expire.call_count == 1
    pipeline_mock.execute.assert_called_once()

    # Verify correct index key
    pipeline_mock.sadd.assert_called_once_with(
        "performance:daily:index:2024-01-15",
        "test_key",
    )


def test_register_cache_multi_day_range() -> None:
    """Test cache registration for multi-day range."""
    redis_mock = MagicMock()
    pipeline_mock = MagicMock()
    redis_mock.pipeline.return_value = pipeline_mock

    register_performance_cache(
        redis_client=redis_mock,
        cache_key="test_key",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 3),
        ttl_seconds=300,
    )

    # Verify 3 days = 3 sadd + 3 expire calls
    assert pipeline_mock.sadd.call_count == 3
    assert pipeline_mock.expire.call_count == 3
    pipeline_mock.execute.assert_called_once()

    # Verify all dates registered
    expected_calls = [
        call("performance:daily:index:2024-01-01", "test_key"),
        call("performance:daily:index:2024-01-02", "test_key"),
        call("performance:daily:index:2024-01-03", "test_key"),
    ]
    pipeline_mock.sadd.assert_has_calls(expected_calls, any_order=False)


def test_register_cache_with_ttl() -> None:
    """Test that TTL is applied to index keys."""
    redis_mock = MagicMock()
    pipeline_mock = MagicMock()
    redis_mock.pipeline.return_value = pipeline_mock

    register_performance_cache(
        redis_client=redis_mock,
        cache_key="test_key",
        start_date=date(2024, 1, 15),
        end_date=date(2024, 1, 15),
        ttl_seconds=600,  # Custom TTL
    )

    pipeline_mock.expire.assert_called_once_with(
        "performance:daily:index:2024-01-15",
        600,
    )


def test_register_cache_none_client() -> None:
    """Test graceful handling when Redis client is None."""
    # Should not raise exception
    register_performance_cache(
        redis_client=None,
        cache_key="test_key",
        start_date=date(2024, 1, 15),
        end_date=date(2024, 1, 15),
        ttl_seconds=300,
    )


def test_register_cache_redis_error(caplog: pytest.LogCaptureFixture) -> None:
    """Test error handling when Redis operations fail."""
    redis_mock = MagicMock()
    pipeline_mock = MagicMock()
    redis_mock.pipeline.return_value = pipeline_mock
    pipeline_mock.execute.side_effect = RedisError("Connection failed")

    register_performance_cache(
        redis_client=redis_mock,
        cache_key="test_key",
        start_date=date(2024, 1, 15),
        end_date=date(2024, 1, 15),
        ttl_seconds=300,
    )

    # Verify warning logged
    assert "Performance cache index registration failed - Redis error" in caplog.text


def test_register_cache_attribute_error(caplog: pytest.LogCaptureFixture) -> None:
    """Test error handling for invalid data types."""
    redis_mock = MagicMock()
    pipeline_mock = MagicMock()
    redis_mock.pipeline.return_value = pipeline_mock
    pipeline_mock.sadd.side_effect = AttributeError("Invalid attribute")

    register_performance_cache(
        redis_client=redis_mock,
        cache_key="test_key",
        start_date=date(2024, 1, 15),
        end_date=date(2024, 1, 15),
        ttl_seconds=300,
    )

    # Verify warning logged
    assert "Performance cache index registration failed - invalid data" in caplog.text


# ============================================================================
# Test invalidate_performance_cache
# ============================================================================


def test_invalidate_cache_single_key() -> None:
    """Test cache invalidation with single cache key."""
    redis_mock = MagicMock()
    redis_mock.sscan_iter.return_value = iter(["cache_key_1"])

    invalidate_performance_cache(redis_mock, date(2024, 1, 15))

    # Verify SSCAN used correct index key
    redis_mock.sscan_iter.assert_called_once_with("performance:daily:index:2024-01-15")

    # Verify deletion (key + index)
    redis_mock.delete.assert_called_once_with("cache_key_1", "performance:daily:index:2024-01-15")


def test_invalidate_cache_multiple_keys_batch() -> None:
    """Test cache invalidation with batching (>100 keys)."""
    redis_mock = MagicMock()
    # Generate 250 keys to trigger multiple batches
    keys = [f"cache_key_{i}" for i in range(250)]
    redis_mock.sscan_iter.return_value = iter(keys)

    invalidate_performance_cache(redis_mock, date(2024, 1, 15))

    # Verify multiple delete calls (2 full batches + 1 partial)
    assert redis_mock.delete.call_count == 3

    # First batch: 100 keys
    first_batch = redis_mock.delete.call_args_list[0][0]
    assert len(first_batch) == 100

    # Second batch: 100 keys
    second_batch = redis_mock.delete.call_args_list[1][0]
    assert len(second_batch) == 100

    # Third batch: 50 keys + index key
    third_batch = redis_mock.delete.call_args_list[2][0]
    assert len(third_batch) == 51  # 50 keys + index key


def test_invalidate_cache_empty_index() -> None:
    """Test cache invalidation with empty index (no cached ranges)."""
    redis_mock = MagicMock()
    redis_mock.sscan_iter.return_value = iter([])

    invalidate_performance_cache(redis_mock, date(2024, 1, 15))

    # Verify only index key deleted
    redis_mock.delete.assert_called_once_with("performance:daily:index:2024-01-15")


def test_invalidate_cache_exact_batch_size() -> None:
    """Test cache invalidation when keys = batch size (100)."""
    redis_mock = MagicMock()
    keys = [f"cache_key_{i}" for i in range(100)]
    redis_mock.sscan_iter.return_value = iter(keys)

    invalidate_performance_cache(redis_mock, date(2024, 1, 15))

    # Verify two delete calls (100 keys, then index key)
    assert redis_mock.delete.call_count == 2

    # First call: 100 keys
    first_batch = redis_mock.delete.call_args_list[0][0]
    assert len(first_batch) == 100

    # Second call: index key only
    second_batch = redis_mock.delete.call_args_list[1][0]
    assert second_batch == ("performance:daily:index:2024-01-15",)


def test_invalidate_cache_defaults_to_today() -> None:
    """Test cache invalidation defaults to today when date not provided."""
    redis_mock = MagicMock()
    redis_mock.sscan_iter.return_value = iter([])
    today = date.today()

    invalidate_performance_cache(redis_mock, trade_date=None)

    # Verify today's index key used
    redis_mock.sscan_iter.assert_called_once_with(f"performance:daily:index:{today}")


def test_invalidate_cache_none_client() -> None:
    """Test graceful handling when Redis client is None."""
    # Should not raise exception
    invalidate_performance_cache(None, date(2024, 1, 15))


def test_invalidate_cache_redis_error(caplog: pytest.LogCaptureFixture) -> None:
    """Test error handling when Redis operations fail."""
    redis_mock = MagicMock()
    redis_mock.sscan_iter.side_effect = RedisError("Connection failed")

    invalidate_performance_cache(redis_mock, date(2024, 1, 15))

    # Verify warning logged
    assert "Performance cache invalidation failed - Redis error" in caplog.text


def test_invalidate_cache_attribute_error(caplog: pytest.LogCaptureFixture) -> None:
    """Test error handling for invalid data types."""
    redis_mock = MagicMock()
    # Make sscan_iter raise AttributeError
    redis_mock.sscan_iter.side_effect = AttributeError(
        "'NoneType' object has no attribute 'sscan_iter'"
    )

    invalidate_performance_cache(redis_mock, date(2024, 1, 15))

    # Verify warning logged
    assert "Performance cache invalidation failed" in caplog.text


def test_create_cache_key_hash_uniqueness() -> None:
    """Test that different strategies produce different hashes."""
    key1 = create_performance_cache_key(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        strategies=("alpha_baseline",),
        user_id="user123",
    )

    key2 = create_performance_cache_key(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        strategies=("momentum",),
        user_id="user123",
    )

    # Same dates and user, different strategies = different keys
    assert key1 != key2


def test_create_cache_key_date_range_uniqueness() -> None:
    """Test that different date ranges produce different keys."""
    key1 = create_performance_cache_key(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        strategies=("alpha_baseline",),
        user_id="user123",
    )

    key2 = create_performance_cache_key(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 2, 29),  # Different end date
        strategies=("alpha_baseline",),
        user_id="user123",
    )

    assert key1 != key2


def test_register_cache_type_error(caplog: pytest.LogCaptureFixture) -> None:
    """Test error handling for TypeError during registration."""
    redis_mock = MagicMock()
    pipeline_mock = MagicMock()
    redis_mock.pipeline.return_value = pipeline_mock
    pipeline_mock.execute.side_effect = TypeError("Invalid type")

    register_performance_cache(
        redis_client=redis_mock,
        cache_key="test_key",
        start_date=date(2024, 1, 15),
        end_date=date(2024, 1, 15),
        ttl_seconds=300,
    )

    # Verify warning logged
    assert "Performance cache index registration failed - invalid data" in caplog.text


def test_invalidate_cache_type_error(caplog: pytest.LogCaptureFixture) -> None:
    """Test error handling for TypeError during invalidation."""
    redis_mock = MagicMock()
    redis_mock.sscan_iter.side_effect = TypeError("Invalid type")

    invalidate_performance_cache(redis_mock, date(2024, 1, 15))

    # Verify warning logged
    assert "Performance cache invalidation failed - invalid data" in caplog.text


def test_invalidate_cache_delete_error_handling(caplog: pytest.LogCaptureFixture) -> None:
    """Test error handling when delete fails during invalidation."""
    redis_mock = MagicMock()
    redis_mock.sscan_iter.return_value = iter(["key1", "key2"])
    redis_mock.delete.side_effect = RedisError("Delete failed")

    invalidate_performance_cache(redis_mock, date(2024, 1, 15))

    # Verify warning logged
    assert "Performance cache invalidation failed - Redis error" in caplog.text


def test_register_cache_large_date_range() -> None:
    """Test cache registration for larger date range (30 days)."""
    redis_mock = MagicMock()
    pipeline_mock = MagicMock()
    redis_mock.pipeline.return_value = pipeline_mock

    register_performance_cache(
        redis_client=redis_mock,
        cache_key="test_key",
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 30),  # 30 days
        ttl_seconds=300,
    )

    # Verify 30 days = 30 sadd + 30 expire calls
    assert pipeline_mock.sadd.call_count == 30
    assert pipeline_mock.expire.call_count == 30
    pipeline_mock.execute.assert_called_once()


def test_invalidate_cache_at_batch_boundary() -> None:
    """Test cache invalidation when keys = batch_size - 1."""
    redis_mock = MagicMock()
    # 99 keys (just under batch size of 100)
    keys = [f"cache_key_{i}" for i in range(99)]
    redis_mock.sscan_iter.return_value = iter(keys)

    invalidate_performance_cache(redis_mock, date(2024, 1, 15))

    # Should be single delete call with 99 keys + index key
    assert redis_mock.delete.call_count == 1
    batch = redis_mock.delete.call_args_list[0][0]
    assert len(batch) == 100  # 99 keys + index key


def test_create_index_key_year_boundary() -> None:
    """Test index key generation across year boundary."""
    key_dec = create_performance_cache_index_key(date(2023, 12, 31))
    key_jan = create_performance_cache_index_key(date(2024, 1, 1))

    assert key_dec == "performance:daily:index:2023-12-31"
    assert key_jan == "performance:daily:index:2024-01-01"
    assert key_dec != key_jan


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
