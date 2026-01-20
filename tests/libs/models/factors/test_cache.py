"""Tests for DiskExpressionCache."""

import tempfile
from datetime import date
from pathlib import Path

import polars as pl
import pytest

from libs.models.factors.cache import DiskExpressionCache


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

    def test_get_stats_empty_cache(self, cache: DiskExpressionCache) -> None:
        """Test cache statistics with no entries."""
        stats = cache.get_stats()

        assert stats["entry_count"] == 0
        assert stats["total_size_bytes"] == 0
        assert stats["oldest_entry"] is None
        assert stats["newest_entry"] is None

    def test_get_stats_multiple_entries(self, cache: DiskExpressionCache) -> None:
        """Test cache statistics with multiple entries."""
        import time

        # Add multiple entries with slight delay to ensure different mtimes
        cache.get_or_compute(
            factor_name="factor_1",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_123",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_123",
            compute_fn=lambda: pl.DataFrame({"a": [1, 2, 3]}),
        )
        time.sleep(0.01)
        cache.get_or_compute(
            factor_name="factor_2",
            as_of_date=date(2024, 1, 16),
            snapshot_id="snap_456",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_456",
            compute_fn=lambda: pl.DataFrame({"b": [4, 5, 6, 7]}),
        )

        stats = cache.get_stats()

        assert stats["entry_count"] == 2
        assert stats["total_size_bytes"] > 0
        assert stats["oldest_entry"] is not None
        assert stats["newest_entry"] is not None

    def test_get_returns_none_when_expired(self, cache_dir: Path) -> None:
        """Test get returns None when cached entry is expired (TTL exceeded)."""
        # Create cache with very short TTL
        cache = DiskExpressionCache(cache_dir, ttl_days=0)

        # Set a value directly
        cache.set(
            factor_name="test_factor",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_123",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_123",
            data=pl.DataFrame({"a": [1, 2, 3]}),
        )

        # Get should return None because TTL=0 means expired immediately
        result = cache.get(
            factor_name="test_factor",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_123",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_123",
        )

        assert result is None

    def test_invalidate_by_snapshot(self, cache: DiskExpressionCache) -> None:
        """Test invalidation by snapshot ID."""
        # Create entries with same snapshot
        cache.get_or_compute(
            factor_name="factor_1",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_to_invalidate",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_123",
            compute_fn=lambda: pl.DataFrame({"a": [1]}),
        )
        cache.get_or_compute(
            factor_name="factor_2",
            as_of_date=date(2024, 1, 16),
            snapshot_id="snap_to_invalidate",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_456",
            compute_fn=lambda: pl.DataFrame({"b": [2]}),
        )
        # Entry with different snapshot should survive
        cache.get_or_compute(
            factor_name="factor_3",
            as_of_date=date(2024, 1, 17),
            snapshot_id="snap_keep",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_789",
            compute_fn=lambda: pl.DataFrame({"c": [3]}),
        )

        # Invalidate by snapshot
        count = cache.invalidate_by_snapshot("snap_to_invalidate")

        assert count == 2

        # Entries with invalidated snapshot should be gone
        assert (
            cache.get(
                factor_name="factor_1",
                as_of_date=date(2024, 1, 15),
                snapshot_id="snap_to_invalidate",
                version_ids={"crsp": "v1.0.0"},
                config_hash="cfg_123",
            )
            is None
        )

        # Entry with different snapshot should still exist
        result = cache.get(
            factor_name="factor_3",
            as_of_date=date(2024, 1, 17),
            snapshot_id="snap_keep",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_789",
        )
        assert result is not None

    def test_invalidate_by_dataset_update(self, cache: DiskExpressionCache) -> None:
        """Test invalidation by dataset version update."""
        # Create entries with different dataset versions
        cache.get_or_compute(
            factor_name="factor_1",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_123",
            version_ids={"crsp": "v1.0.0", "compustat": "v2.0.0"},
            config_hash="cfg_123",
            compute_fn=lambda: pl.DataFrame({"a": [1]}),
        )
        cache.get_or_compute(
            factor_name="factor_2",
            as_of_date=date(2024, 1, 16),
            snapshot_id="snap_456",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_456",
            compute_fn=lambda: pl.DataFrame({"b": [2]}),
        )
        # Entry with new version should survive
        cache.get_or_compute(
            factor_name="factor_3",
            as_of_date=date(2024, 1, 17),
            snapshot_id="snap_789",
            version_ids={"crsp": "v2.0.0"},
            config_hash="cfg_789",
            compute_fn=lambda: pl.DataFrame({"c": [3]}),
        )

        # Invalidate entries with old crsp version
        count = cache.invalidate_by_dataset_update("crsp", "v2.0.0")

        assert count == 2  # factor_1 and factor_2 had crsp v1.0.0

        # Entry with new version should still exist
        result = cache.get(
            factor_name="factor_3",
            as_of_date=date(2024, 1, 17),
            snapshot_id="snap_789",
            version_ids={"crsp": "v2.0.0"},
            config_hash="cfg_789",
        )
        assert result is not None

    def test_invalidate_all(self, cache: DiskExpressionCache) -> None:
        """Test invalidate all cache entries."""
        # Create multiple entries
        cache.get_or_compute(
            factor_name="factor_1",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_123",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_123",
            compute_fn=lambda: pl.DataFrame({"a": [1]}),
        )
        cache.get_or_compute(
            factor_name="factor_2",
            as_of_date=date(2024, 1, 16),
            snapshot_id="snap_456",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_456",
            compute_fn=lambda: pl.DataFrame({"b": [2]}),
        )

        # Invalidate all
        count = cache.invalidate_all()

        assert count == 2

        # All entries should be gone
        stats = cache.get_stats()
        assert stats["entry_count"] == 0

    def test_invalidate_all_by_factor_name(self, cache: DiskExpressionCache) -> None:
        """Test invalidate all cache entries for a specific factor."""
        # Create entries for multiple factors
        cache.get_or_compute(
            factor_name="momentum",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_123",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_123",
            compute_fn=lambda: pl.DataFrame({"a": [1]}),
        )
        cache.get_or_compute(
            factor_name="momentum",
            as_of_date=date(2024, 1, 16),
            snapshot_id="snap_456",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_456",
            compute_fn=lambda: pl.DataFrame({"b": [2]}),
        )
        cache.get_or_compute(
            factor_name="value",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_123",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_789",
            compute_fn=lambda: pl.DataFrame({"c": [3]}),
        )

        # Invalidate only momentum factor entries
        count = cache.invalidate_all(factor_name="momentum")

        assert count == 2

        # Value factor should still exist
        result = cache.get(
            factor_name="value",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_123",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_789",
        )
        assert result is not None

    def test_cleanup_expired_with_orphaned_files(self, cache_dir: Path) -> None:
        """Test cleanup_expired removes orphaned files not in index."""
        cache = DiskExpressionCache(cache_dir, ttl_days=7)

        # Create a legitimate cache entry
        cache.get_or_compute(
            factor_name="factor_1",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_123",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_123",
            compute_fn=lambda: pl.DataFrame({"a": [1]}),
        )

        # Create an orphaned parquet file (not in index)
        orphan_path = cache_dir / "orphan_file.parquet"
        pl.DataFrame({"orphan": [1, 2, 3]}).write_parquet(orphan_path)

        # Run cleanup (with high TTL so legitimate entry survives)
        count = cache.cleanup_expired(ttl_days=365)

        # Orphan should be removed
        assert not orphan_path.exists()
        assert count >= 1  # At least the orphan was removed

    def test_cleanup_expired_with_ttl_override(self, cache_dir: Path) -> None:
        """Test cleanup_expired uses provided TTL override."""
        cache = DiskExpressionCache(cache_dir, ttl_days=365)

        # Create a cache entry
        cache.get_or_compute(
            factor_name="factor_1",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_123",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_123",
            compute_fn=lambda: pl.DataFrame({"a": [1]}),
        )

        # Cleanup with TTL=0 should remove even fresh entries
        count = cache.cleanup_expired(ttl_days=0)

        assert count >= 1
        stats = cache.get_stats()
        assert stats["entry_count"] == 0
