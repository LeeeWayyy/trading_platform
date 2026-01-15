"""Tests for exception handling in DiskExpressionCache.

These tests verify that the cache properly handles various error conditions
with specific exception types and appropriate logging.
"""

import tempfile
from datetime import date
from pathlib import Path
from unittest.mock import patch

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


class TestCacheReadExceptionHandling:
    """Tests for exception handling during cache read operations."""

    def test_get_or_compute_handles_oserror_on_read(
        self, cache: DiskExpressionCache, cache_dir: Path
    ) -> None:
        """Test that OSError during cache read triggers graceful degradation."""
        # First, create a valid cache entry
        cache.get_or_compute(
            factor_name="test_factor",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_123",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_123",
            compute_fn=lambda: pl.DataFrame({"a": [1, 2, 3]}),
        )

        # Now mock pl.read_parquet to raise OSError
        compute_called = [False]

        def compute_fn() -> pl.DataFrame:
            compute_called[0] = True
            return pl.DataFrame({"a": [4, 5, 6]})

        with patch("polars.read_parquet", side_effect=OSError("Disk read error")):
            df, was_cached = cache.get_or_compute(
                factor_name="test_factor",
                as_of_date=date(2024, 1, 15),
                snapshot_id="snap_123",
                version_ids={"crsp": "v1.0.0"},
                config_hash="cfg_123",
                compute_fn=compute_fn,
            )

            # Should have recomputed due to read error
            assert compute_called[0] is True
            assert was_cached is False
            assert len(df) == 3

    def test_get_or_compute_handles_compute_error_on_read(
        self, cache: DiskExpressionCache, cache_dir: Path
    ) -> None:
        """Test that ComputeError during cache read triggers cache cleanup."""
        # First, create a valid cache entry
        cache.get_or_compute(
            factor_name="test_factor",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_123",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_123",
            compute_fn=lambda: pl.DataFrame({"a": [1, 2, 3]}),
        )

        # Now mock pl.read_parquet to raise ComputeError (corrupted data)
        compute_called = [False]

        def compute_fn() -> pl.DataFrame:
            compute_called[0] = True
            return pl.DataFrame({"a": [4, 5, 6]})

        with patch("polars.read_parquet", side_effect=pl.ComputeError("Invalid Parquet")):
            df, was_cached = cache.get_or_compute(
                factor_name="test_factor",
                as_of_date=date(2024, 1, 15),
                snapshot_id="snap_123",
                version_ids={"crsp": "v1.0.0"},
                config_hash="cfg_123",
                compute_fn=compute_fn,
            )

            # Should have recomputed due to corrupted cache
            assert compute_called[0] is True
            assert was_cached is False
            assert len(df) == 3

    def test_get_returns_none_on_oserror(self, cache: DiskExpressionCache) -> None:
        """Test that get() returns None on OSError during read."""
        # First, create a valid cache entry
        cache.set(
            factor_name="test_factor",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_123",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_123",
            data=pl.DataFrame({"a": [1, 2, 3]}),
        )

        # Now mock pl.read_parquet to raise OSError
        with patch("polars.read_parquet", side_effect=OSError("Disk read error")):
            result = cache.get(
                factor_name="test_factor",
                as_of_date=date(2024, 1, 15),
                snapshot_id="snap_123",
                version_ids={"crsp": "v1.0.0"},
                config_hash="cfg_123",
            )

            assert result is None

    def test_get_returns_none_on_compute_error(self, cache: DiskExpressionCache) -> None:
        """Test that get() returns None on ComputeError (corrupted cache)."""
        # First, create a valid cache entry
        cache.set(
            factor_name="test_factor",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_123",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_123",
            data=pl.DataFrame({"a": [1, 2, 3]}),
        )

        # Now mock pl.read_parquet to raise ComputeError
        with patch("polars.read_parquet", side_effect=pl.ComputeError("Invalid Parquet")):
            result = cache.get(
                factor_name="test_factor",
                as_of_date=date(2024, 1, 15),
                snapshot_id="snap_123",
                version_ids={"crsp": "v1.0.0"},
                config_hash="cfg_123",
            )

            assert result is None


class TestAtomicWriteExceptionHandling:
    """Tests for exception handling during atomic write operations."""

    def test_atomic_write_handles_oserror(self, cache: DiskExpressionCache) -> None:
        """Test that atomic write handles OSError during file operations."""
        df = pl.DataFrame({"a": [1, 2, 3]})
        path = cache.cache_dir / "test.parquet"

        # Mock shutil.move to raise OSError
        with patch("shutil.move", side_effect=OSError("Disk full")):
            with pytest.raises(OSError, match="Disk full"):
                cache._atomic_write_parquet(path, df)

    def test_atomic_write_handles_compute_error(self, cache: DiskExpressionCache) -> None:
        """Test that atomic write handles ComputeError during Parquet write."""
        df = pl.DataFrame({"a": [1, 2, 3]})
        path = cache.cache_dir / "test.parquet"

        # Mock df.write_parquet to raise ComputeError
        with patch.object(pl.DataFrame, "write_parquet", side_effect=pl.ComputeError("Write error")):
            with pytest.raises(pl.ComputeError, match="Write error"):
                cache._atomic_write_parquet(path, df)


class TestIndexUpdateExceptionHandling:
    """Tests for exception handling during index updates."""

    def test_get_or_compute_rollback_on_index_failure(
        self, cache: DiskExpressionCache, cache_dir: Path
    ) -> None:
        """Test that cache file is rolled back if index update fails."""
        import sqlite3

        compute_called = [False]

        def compute_fn() -> pl.DataFrame:
            compute_called[0] = True
            return pl.DataFrame({"a": [1, 2, 3]})

        # Mock _update_index to raise sqlite3.Error
        with patch.object(
            cache, "_update_index", side_effect=sqlite3.Error("Database locked")
        ):
            with pytest.raises(sqlite3.Error, match="Database locked"):
                cache.get_or_compute(
                    factor_name="test_factor",
                    as_of_date=date(2024, 1, 15),
                    snapshot_id="snap_123",
                    version_ids={"crsp": "v1.0.0"},
                    config_hash="cfg_123",
                    compute_fn=compute_fn,
                )

            # Verify compute was called
            assert compute_called[0] is True

            # Verify that cache file was rolled back (should not exist)
            cache_files = list(cache_dir.glob("test_factor_*.parquet"))
            assert len(cache_files) == 0


class TestLegacyIndexMigrationExceptionHandling:
    """Tests for exception handling during legacy index migration."""

    def test_migrate_legacy_index_handles_oserror(
        self, cache: DiskExpressionCache, cache_dir: Path
    ) -> None:
        """Test that legacy index migration handles OSError gracefully."""
        legacy_path = cache_dir / ".cache_index.json"
        legacy_path.write_text('{"test": {"snapshot_id": "snap_123"}}')

        # Mock open to raise OSError
        with patch("builtins.open", side_effect=OSError("Permission denied")):
            # Should not raise, just log and skip migration
            cache._migrate_legacy_index()

    def test_migrate_legacy_index_handles_json_decode_error(
        self, cache: DiskExpressionCache, cache_dir: Path
    ) -> None:
        """Test that legacy index migration handles corrupted JSON gracefully."""
        legacy_path = cache_dir / ".cache_index.json"
        legacy_path.write_text("{invalid json")

        # Should not raise, just log and skip migration
        cache._migrate_legacy_index()

    def test_migrate_legacy_index_handles_value_error(
        self, cache: DiskExpressionCache, cache_dir: Path
    ) -> None:
        """Test that legacy index migration handles invalid data format gracefully."""
        legacy_path = cache_dir / ".cache_index.json"
        legacy_path.write_text("null")  # Valid JSON but not expected format

        # Should not raise, just log and skip migration
        cache._migrate_legacy_index()
