"""Tests for DiskExpressionCache."""

import tempfile
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from libs.factors.cache import DiskExpressionCache


@pytest.fixture()
def cache_dir() -> Path:
    """Create temporary cache directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture()
def cache(cache_dir: Path) -> DiskExpressionCache:
    """Create cache instance."""
    return DiskExpressionCache(cache_dir, ttl_days=7)


class TestDiskExpressionCache:
    """Tests for DiskExpressionCache."""

    def test_build_version_id_string(self, cache: DiskExpressionCache) -> None:
        """Test deterministic version ID string building."""
        version_ids = {"crsp": "v1.2.3", "compustat": "v1.0.1"}
        result = cache._build_version_id_string(version_ids)

        # Should be sorted alphabetically
        assert result == "compustat-v1.0.1_crsp-v1.2.3"

    def test_build_version_id_string_deterministic(self, cache: DiskExpressionCache) -> None:
        """Test version ID string is deterministic."""
        version_ids_1 = {"crsp": "v1.2.3", "compustat": "v1.0.1"}
        version_ids_2 = {"compustat": "v1.0.1", "crsp": "v1.2.3"}  # Different order

        result_1 = cache._build_version_id_string(version_ids_1)
        result_2 = cache._build_version_id_string(version_ids_2)

        assert result_1 == result_2

    def test_build_key(self, cache: DiskExpressionCache) -> None:
        """Test cache key building."""
        key = cache._build_key(
            factor_name="momentum_12m",
            as_of_date=date(2024, 1, 15),
            version_ids={"crsp": "v1.2.3"},
            snapshot_id="snap_abc123",
            config_hash="cfg_def456",
        )

        # All 5 components should be present
        assert "momentum_12m" in key
        assert "2024-01-15" in key
        assert "crsp-v1.2.3" in key
        assert "snap_abc123" in key
        assert "cfg_def456" in key

    def test_get_or_compute_cache_miss(self, cache: DiskExpressionCache) -> None:
        """Test cache miss triggers computation."""
        compute_called = [False]

        def compute_fn() -> pl.DataFrame:
            compute_called[0] = True
            return pl.DataFrame({"a": [1, 2, 3]})

        df, was_cached = cache.get_or_compute(
            factor_name="test_factor",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_123",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_123",
            compute_fn=compute_fn,
        )

        assert compute_called[0] is True
        assert was_cached is False
        assert len(df) == 3

    def test_get_or_compute_cache_hit(self, cache: DiskExpressionCache) -> None:
        """Test cache hit returns cached value."""
        # First call - cache miss
        cache.get_or_compute(
            factor_name="test_factor",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_123",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_123",
            compute_fn=lambda: pl.DataFrame({"a": [1, 2, 3]}),
        )

        # Second call - cache hit
        compute_called = [False]

        def compute_fn() -> pl.DataFrame:
            compute_called[0] = True
            return pl.DataFrame({"a": [4, 5, 6]})

        df, was_cached = cache.get_or_compute(
            factor_name="test_factor",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_123",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_123",
            compute_fn=compute_fn,
        )

        assert compute_called[0] is False
        assert was_cached is True
        # Should return original values
        assert df["a"].to_list() == [1, 2, 3]

    def test_different_snapshot_id_misses(self, cache: DiskExpressionCache) -> None:
        """Test different snapshot_id causes cache miss."""
        # First call
        cache.get_or_compute(
            factor_name="test_factor",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_123",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_123",
            compute_fn=lambda: pl.DataFrame({"a": [1, 2, 3]}),
        )

        # Different snapshot - should miss
        compute_called = [False]

        def compute_fn() -> pl.DataFrame:
            compute_called[0] = True
            return pl.DataFrame({"a": [4, 5, 6]})

        df, was_cached = cache.get_or_compute(
            factor_name="test_factor",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_456",  # Different snapshot
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_123",
            compute_fn=compute_fn,
        )

        assert compute_called[0] is True
        assert was_cached is False

    def test_different_config_hash_misses(self, cache: DiskExpressionCache) -> None:
        """Test different config_hash causes cache miss."""
        # First call with config_123
        cache.get_or_compute(
            factor_name="test_factor",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_123",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_123",
            compute_fn=lambda: pl.DataFrame({"a": [1, 2, 3]}),
        )

        # Second call with different config - should miss
        compute_called = {"called": False}

        def compute_fn() -> pl.DataFrame:
            compute_called["called"] = True
            return pl.DataFrame({"a": [4, 5, 6]})

        df, was_cached = cache.get_or_compute(
            factor_name="test_factor",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_123",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_456",  # Different config
            compute_fn=compute_fn,
        )

        # Should have computed (cache miss due to different config)
        assert compute_called["called"] is True
        assert was_cached is False

    def test_get_returns_none_when_not_cached(self, cache: DiskExpressionCache) -> None:
        """Test get returns None for uncached key."""
        result = cache.get(
            factor_name="nonexistent",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_123",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_123",
        )

        assert result is None

    def test_invalidate_by_config_change(self, cache: DiskExpressionCache) -> None:
        """Test invalidation by config change."""
        # Create some cache entries
        cache.get_or_compute(
            factor_name="momentum_12m",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_123",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_123",
            compute_fn=lambda: pl.DataFrame({"a": [1]}),
        )

        # Invalidate
        count = cache.invalidate_by_config_change("momentum_12m")

        assert count >= 1

        # Should be gone
        result = cache.get(
            factor_name="momentum_12m",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_123",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_123",
        )
        assert result is None

    def test_cleanup_expired(self, cache_dir: Path) -> None:
        """Test expired entry cleanup."""
        # Create cache with very short TTL
        cache = DiskExpressionCache(cache_dir, ttl_days=0)

        # Add entry
        cache.get_or_compute(
            factor_name="test_factor",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_123",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_123",
            compute_fn=lambda: pl.DataFrame({"a": [1]}),
        )

        # Cleanup should remove it (TTL=0)
        count = cache.cleanup_expired()
        assert count >= 1

    def test_get_stats(self, cache: DiskExpressionCache) -> None:
        """Test cache statistics."""
        # Add some entries
        cache.get_or_compute(
            factor_name="factor_1",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_123",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_123",
            compute_fn=lambda: pl.DataFrame({"a": [1, 2, 3]}),
        )

        stats = cache.get_stats()

        assert stats["entry_count"] == 1
        assert stats["total_size_bytes"] > 0
        assert stats["oldest_entry"] is not None
        assert stats["newest_entry"] is not None
