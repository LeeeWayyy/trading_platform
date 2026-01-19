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
        with patch.object(
            pl.DataFrame, "write_parquet", side_effect=pl.ComputeError("Write error")
        ):
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
        with patch.object(cache, "_update_index", side_effect=sqlite3.Error("Database locked")):
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

    def test_migrate_legacy_index_with_version_ids(
        self, cache: DiskExpressionCache, cache_dir: Path
    ) -> None:
        """Test legacy index migration with version_ids data."""
        import json

        legacy_path = cache_dir / ".cache_index.json"
        legacy_data = {
            "test_file.parquet": {
                "snapshot_id": "snap_123",
                "config_hash": "cfg_456",
                "version_ids": {"crsp": "v1.0.0", "compustat": "v2.0.0"},
            },
            "test_file2.parquet": {
                "snapshot_id": "snap_789",
                "config_hash": "cfg_abc",
                "version_ids": None,  # Test None version_ids
            },
        }
        legacy_path.write_text(json.dumps(legacy_data))

        # Migration should succeed and remove legacy file
        cache._migrate_legacy_index()

        # Legacy file should be removed after successful migration
        assert not legacy_path.exists()

    def test_migrate_legacy_index_cleanup_failure(
        self, cache: DiskExpressionCache, cache_dir: Path
    ) -> None:
        """Test legacy index migration handles cleanup failure gracefully."""
        import json

        legacy_path = cache_dir / ".cache_index.json"
        legacy_data = {"test_file.parquet": {"snapshot_id": "snap_123"}}
        legacy_path.write_text(json.dumps(legacy_data))

        # Mock unlink to fail after migration
        original_unlink = Path.unlink

        def failing_unlink(self, *args, **kwargs):
            if str(self).endswith(".cache_index.json"):
                raise OSError("Permission denied")
            return original_unlink(self, *args, **kwargs)

        with patch.object(Path, "unlink", failing_unlink):
            # Should not raise, just log the failure
            cache._migrate_legacy_index()

        # Legacy file still exists due to unlink failure
        assert legacy_path.exists()


class TestCacheCleanupExceptionHandling:
    """Tests for exception handling during cache cleanup operations."""

    def test_cleanup_expired_handles_oserror_on_delete(
        self, cache: DiskExpressionCache, cache_dir: Path
    ) -> None:
        """Test cleanup_expired handles OSError when deleting files."""
        # Create a cache entry
        cache.get_or_compute(
            factor_name="test_factor",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_123",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_123",
            compute_fn=lambda: pl.DataFrame({"a": [1, 2, 3]}),
        )

        # Mock Path.unlink to raise OSError for parquet files
        original_unlink = Path.unlink

        def failing_unlink(self, *args, **kwargs):
            if str(self).endswith(".parquet"):
                raise OSError("Permission denied")
            return original_unlink(self, *args, **kwargs)

        with patch.object(Path, "unlink", failing_unlink):
            # Should not raise, just log the failure
            count = cache.cleanup_expired(ttl_days=0)

        # Count should be 0 since unlink failed
        assert count == 0

    def test_cleanup_expired_handles_orphan_delete_failure(
        self, cache: DiskExpressionCache, cache_dir: Path
    ) -> None:
        """Test cleanup_expired handles failure when deleting orphaned files."""
        # Create an orphaned parquet file (not in index)
        orphan_path = cache_dir / "orphan_file.parquet"
        pl.DataFrame({"orphan": [1, 2, 3]}).write_parquet(orphan_path)

        # Mock Path.unlink to raise OSError for the orphan
        original_unlink = Path.unlink

        def failing_unlink(self, *args, **kwargs):
            if "orphan" in str(self):
                raise OSError("Permission denied")
            return original_unlink(self, *args, **kwargs)

        with patch.object(Path, "unlink", failing_unlink):
            # Should not raise, just log the failure
            cache.cleanup_expired(ttl_days=365)

        # Orphan should still exist due to unlink failure
        assert orphan_path.exists()

    def test_invalidate_by_snapshot_handles_oserror(
        self, cache: DiskExpressionCache, cache_dir: Path
    ) -> None:
        """Test invalidate_by_snapshot handles OSError when deleting files."""
        # Create a cache entry
        cache.get_or_compute(
            factor_name="test_factor",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_to_delete",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_123",
            compute_fn=lambda: pl.DataFrame({"a": [1, 2, 3]}),
        )

        # Mock Path.unlink to raise OSError
        original_unlink = Path.unlink

        def failing_unlink(self, *args, **kwargs):
            if str(self).endswith(".parquet"):
                raise OSError("Permission denied")
            return original_unlink(self, *args, **kwargs)

        with patch.object(Path, "unlink", failing_unlink):
            # Should not raise, just log and return 0
            count = cache.invalidate_by_snapshot("snap_to_delete")

        # Count should be 0 since unlink failed
        assert count == 0

    def test_invalidate_by_dataset_update_handles_oserror(
        self, cache: DiskExpressionCache, cache_dir: Path
    ) -> None:
        """Test invalidate_by_dataset_update handles OSError when deleting files."""
        # Create a cache entry
        cache.get_or_compute(
            factor_name="test_factor",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_123",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_123",
            compute_fn=lambda: pl.DataFrame({"a": [1, 2, 3]}),
        )

        # Mock Path.unlink to raise OSError
        original_unlink = Path.unlink

        def failing_unlink(self, *args, **kwargs):
            if str(self).endswith(".parquet"):
                raise OSError("Permission denied")
            return original_unlink(self, *args, **kwargs)

        with patch.object(Path, "unlink", failing_unlink):
            # Should not raise, just log and return 0
            count = cache.invalidate_by_dataset_update("crsp", "v2.0.0")

        # Count should be 0 since unlink failed
        assert count == 0

    def test_invalidate_by_config_change_handles_oserror(
        self, cache: DiskExpressionCache, cache_dir: Path
    ) -> None:
        """Test invalidate_by_config_change handles OSError when deleting files."""
        # Create a cache entry
        cache.get_or_compute(
            factor_name="momentum",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_123",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_123",
            compute_fn=lambda: pl.DataFrame({"a": [1, 2, 3]}),
        )

        # Mock Path.unlink to raise OSError
        original_unlink = Path.unlink

        def failing_unlink(self, *args, **kwargs):
            if str(self).endswith(".parquet"):
                raise OSError("Permission denied")
            return original_unlink(self, *args, **kwargs)

        with patch.object(Path, "unlink", failing_unlink):
            # Should not raise, just log and return 0
            count = cache.invalidate_by_config_change("momentum")

        # Count should be 0 since unlink failed
        assert count == 0

    def test_invalidate_all_handles_oserror(
        self, cache: DiskExpressionCache, cache_dir: Path
    ) -> None:
        """Test invalidate_all handles OSError when deleting files."""
        # Create cache entries
        cache.get_or_compute(
            factor_name="factor_1",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_123",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_123",
            compute_fn=lambda: pl.DataFrame({"a": [1]}),
        )

        # Mock Path.unlink to raise OSError
        original_unlink = Path.unlink

        def failing_unlink(self, *args, **kwargs):
            if str(self).endswith(".parquet"):
                raise OSError("Permission denied")
            return original_unlink(self, *args, **kwargs)

        with patch.object(Path, "unlink", failing_unlink):
            # Should not raise, just log and return 0
            count = cache.invalidate_all()

        # Count should be 0 since unlink failed
        assert count == 0


class TestCacheCorruptionCleanup:
    """Tests for cache corruption cleanup during read operations."""

    def test_get_or_compute_cleanup_corrupted_cache_failure(
        self, cache: DiskExpressionCache, cache_dir: Path
    ) -> None:
        """Test get_or_compute handles failure to clean up corrupted cache."""
        # First, create a valid cache entry
        cache.get_or_compute(
            factor_name="test_factor",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_123",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_123",
            compute_fn=lambda: pl.DataFrame({"a": [1, 2, 3]}),
        )

        # Track compute calls
        compute_called = [False]

        def compute_fn() -> pl.DataFrame:
            compute_called[0] = True
            return pl.DataFrame({"a": [4, 5, 6]})

        # Mock read_parquet to raise ComputeError (corruption)
        # and mock unlink to fail during cleanup
        original_unlink = Path.unlink

        def failing_unlink(self, *args, **kwargs):
            if str(self).endswith(".parquet"):
                raise OSError("Permission denied")
            return original_unlink(self, *args, **kwargs)

        with (
            patch("polars.read_parquet", side_effect=pl.ComputeError("Corrupted")),
            patch.object(Path, "unlink", failing_unlink),
        ):
            df, was_cached = cache.get_or_compute(
                factor_name="test_factor",
                as_of_date=date(2024, 1, 15),
                snapshot_id="snap_123",
                version_ids={"crsp": "v1.0.0"},
                config_hash="cfg_123",
                compute_fn=compute_fn,
            )

            # Should still compute despite cleanup failure
            assert compute_called[0] is True
            assert was_cached is False

    def test_get_cleanup_corrupted_cache_failure(
        self, cache: DiskExpressionCache, cache_dir: Path
    ) -> None:
        """Test get handles failure to clean up corrupted cache."""
        # Create a valid cache entry
        cache.set(
            factor_name="test_factor",
            as_of_date=date(2024, 1, 15),
            snapshot_id="snap_123",
            version_ids={"crsp": "v1.0.0"},
            config_hash="cfg_123",
            data=pl.DataFrame({"a": [1, 2, 3]}),
        )

        # Mock read_parquet to raise ComputeError (corruption)
        # and mock unlink to fail during cleanup
        original_unlink = Path.unlink

        def failing_unlink(self, *args, **kwargs):
            if str(self).endswith(".parquet"):
                raise OSError("Permission denied")
            return original_unlink(self, *args, **kwargs)

        with (
            patch("polars.read_parquet", side_effect=pl.ComputeError("Corrupted")),
            patch.object(Path, "unlink", failing_unlink),
        ):
            result = cache.get(
                factor_name="test_factor",
                as_of_date=date(2024, 1, 15),
                snapshot_id="snap_123",
                version_ids={"crsp": "v1.0.0"},
                config_hash="cfg_123",
            )

            # Should return None despite cleanup failure
            assert result is None


class TestIndexRollbackExceptionHandling:
    """Tests for exception handling during index rollback."""

    def test_get_or_compute_rollback_file_delete_failure(
        self, cache: DiskExpressionCache, cache_dir: Path
    ) -> None:
        """Test that index failure rollback handles delete failure gracefully."""
        import sqlite3

        compute_called = [False]

        def compute_fn() -> pl.DataFrame:
            compute_called[0] = True
            return pl.DataFrame({"a": [1, 2, 3]})

        # Track unlink calls to fail on rollback
        original_unlink = Path.unlink

        def failing_unlink(self, *args, **kwargs):
            # Fail for parquet files during rollback
            if str(self).endswith(".parquet"):
                raise OSError("Permission denied during rollback")
            return original_unlink(self, *args, **kwargs)

        # Mock _update_index to raise sqlite3.Error, then unlink to fail
        with (
            patch.object(cache, "_update_index", side_effect=sqlite3.Error("DB locked")),
            patch.object(Path, "unlink", failing_unlink),
        ):
            with pytest.raises(sqlite3.Error, match="DB locked"):
                cache.get_or_compute(
                    factor_name="test_factor",
                    as_of_date=date(2024, 1, 15),
                    snapshot_id="snap_123",
                    version_ids={"crsp": "v1.0.0"},
                    config_hash="cfg_123",
                    compute_fn=compute_fn,
                )

            # Compute should have been called
            assert compute_called[0] is True


class TestAtomicWriteTempFileCleanup:
    """Tests for temp file cleanup during atomic write failures."""

    def test_atomic_write_cleanup_temp_file_on_oserror(
        self, cache: DiskExpressionCache
    ) -> None:
        """Test that atomic write cleans up temp file on OSError."""
        df = pl.DataFrame({"a": [1, 2, 3]})
        path = cache.cache_dir / "test.parquet"

        # Track temp file creation
        temp_file_path = [None]
        original_mkstemp = __import__("tempfile").mkstemp

        def tracking_mkstemp(*args, **kwargs):
            fd, temp_path = original_mkstemp(*args, **kwargs)
            temp_file_path[0] = temp_path
            return fd, temp_path

        # Mock shutil.move to raise OSError after temp file is created
        with (
            patch("tempfile.mkstemp", side_effect=tracking_mkstemp),
            patch("shutil.move", side_effect=OSError("Move failed")),
        ):
            with pytest.raises(OSError, match="Move failed"):
                cache._atomic_write_parquet(path, df)

        # Temp file should be cleaned up
        if temp_file_path[0]:
            assert not Path(temp_file_path[0]).exists()

    def test_atomic_write_cleanup_temp_file_on_compute_error(
        self, cache: DiskExpressionCache
    ) -> None:
        """Test that atomic write cleans up temp file on ComputeError."""
        df = pl.DataFrame({"a": [1, 2, 3]})
        path = cache.cache_dir / "test.parquet"

        # Mock write_parquet to raise ComputeError
        with patch.object(
            pl.DataFrame, "write_parquet", side_effect=pl.ComputeError("Write failed")
        ):
            with pytest.raises(pl.ComputeError, match="Write failed"):
                cache._atomic_write_parquet(path, df)

    def test_atomic_write_cleanup_temp_file_failure(
        self, cache: DiskExpressionCache
    ) -> None:
        """Test atomic write handles failure to clean up temp file gracefully."""
        df = pl.DataFrame({"a": [1, 2, 3]})
        path = cache.cache_dir / "test.parquet"

        # Mock to track temp file and fail on cleanup
        original_unlink = Path.unlink

        def failing_unlink(self, *args, **kwargs):
            if ".tmp_" in str(self):
                raise OSError("Cleanup failed")
            return original_unlink(self, *args, **kwargs)

        with (
            patch("shutil.move", side_effect=OSError("Move failed")),
            patch.object(Path, "unlink", failing_unlink),
        ):
            with pytest.raises(OSError, match="Move failed"):
                cache._atomic_write_parquet(path, df)

    def test_atomic_write_compute_error_cleanup_temp_file_failure(
        self, cache: DiskExpressionCache
    ) -> None:
        """Test atomic write handles temp file cleanup failure on ComputeError gracefully."""
        df = pl.DataFrame({"a": [1, 2, 3]})
        path = cache.cache_dir / "test.parquet"

        # Mock to fail on cleanup for temp file
        original_unlink = Path.unlink

        def failing_unlink(self, *args, **kwargs):
            if ".tmp_" in str(self):
                raise OSError("Cleanup failed")
            return original_unlink(self, *args, **kwargs)

        with (
            patch.object(
                pl.DataFrame, "write_parquet", side_effect=pl.ComputeError("Write failed")
            ),
            patch.object(Path, "unlink", failing_unlink),
        ):
            with pytest.raises(pl.ComputeError, match="Write failed"):
                cache._atomic_write_parquet(path, df)


class TestLegacyIndexMigrationValueError:
    """Tests for ValueError handling during legacy index migration."""

    def test_migrate_legacy_index_handles_value_error_from_json_load(
        self, cache: DiskExpressionCache, cache_dir: Path
    ) -> None:
        """Test that legacy index migration handles ValueError from JSON gracefully."""
        legacy_path = cache_dir / ".cache_index.json"
        # Create valid JSON file
        legacy_path.write_text('{"test": "data"}')

        # Mock json.load to raise ValueError (different from JSONDecodeError)
        with patch("json.load", side_effect=ValueError("Invalid value in JSON")):
            # Should not raise, just log and skip migration
            cache._migrate_legacy_index()

        # Legacy file should still exist since migration was skipped
        assert legacy_path.exists()
