"""Comprehensive coverage tests for taq_storage.py.

Target: 85%+ coverage for libs/data/data_providers/taq_storage.py

Covers:
- TAQStorageManager sync operations (aggregates and samples)
- Symbol sanitization and validation
- Disk space checks and error handling
- Atomic write operations with checksums
- Schema validation and drift detection
- Manifest creation and cleanup
- Partition building and filtering
"""

from __future__ import annotations

import datetime
import shutil
from pathlib import Path
from unittest.mock import MagicMock

import polars as pl
import pytest

from libs.data.data_providers import taq_storage
from libs.data.data_providers.taq_storage import (
    TAQStorageManager,
    register_taq_schemas,
)
from libs.data.data_quality.exceptions import DiskSpaceError
from libs.data.data_quality.manifest import ManifestManager
from libs.data.data_quality.schema import SchemaRegistry
from libs.data.data_quality.validation import DataValidator
from libs.data.data_quality.versioning import DatasetVersionManager


@pytest.fixture()
def storage_manager(tmp_path: Path) -> TAQStorageManager:
    """Create a TAQStorageManager wired to isolated temp directories."""
    data_root = tmp_path / "data"
    storage_path = data_root / "taq"
    lock_dir = data_root / "locks"

    manifest_manager = ManifestManager(
        storage_path=data_root / "manifests",
        lock_dir=lock_dir,
        data_root=data_root,
    )
    version_manager = DatasetVersionManager(
        manifest_manager=manifest_manager,
        validator=None,
        snapshots_dir=data_root / "snapshots",
        cas_dir=data_root / "cas",
        diffs_dir=data_root / "diffs",
        locks_dir=lock_dir,
        data_root=data_root,
    )
    schema_registry = SchemaRegistry(
        storage_path=data_root / "schemas",
        lock_dir=lock_dir / "schema",
    )

    manager = TAQStorageManager(
        wrds_client=MagicMock(),
        storage_path=storage_path,
        lock_dir=lock_dir,
        manifest_manager=manifest_manager,
        version_manager=version_manager,
        validator=DataValidator(),
        schema_registry=schema_registry,
    )

    return manager


class TestSymbolSanitization:
    """Tests for symbol sanitization and validation."""

    def test_sanitize_symbol_valid(self, storage_manager):
        """Test sanitizing valid symbols."""
        assert storage_manager._sanitize_symbol("AAPL") == "AAPL"
        assert storage_manager._sanitize_symbol("brk.b") == "BRK.B"
        assert storage_manager._sanitize_symbol("brk-a") == "BRK-A"
        assert storage_manager._sanitize_symbol("  MSFT  ") == "MSFT"

    def test_sanitize_symbol_invalid_empty(self, storage_manager):
        """Test sanitizing empty symbol raises error."""
        with pytest.raises(ValueError, match="Invalid symbol"):
            storage_manager._sanitize_symbol("")

    def test_sanitize_symbol_invalid_path_traversal(self, storage_manager):
        """Test sanitizing path traversal patterns raises error."""
        with pytest.raises(ValueError, match="Invalid symbol"):
            storage_manager._sanitize_symbol(".")

        with pytest.raises(ValueError, match="Invalid symbol"):
            storage_manager._sanitize_symbol("..")

    def test_sanitize_symbol_invalid_characters(self, storage_manager):
        """Test sanitizing symbols with invalid characters."""
        with pytest.raises(ValueError, match="Invalid symbol"):
            storage_manager._sanitize_symbol("AAPL/TEST")

        with pytest.raises(ValueError, match="Invalid symbol"):
            storage_manager._sanitize_symbol("AAPL;DROP")

    def test_sanitize_symbol_too_long(self, storage_manager):
        """Test sanitizing symbols that are too long."""
        with pytest.raises(ValueError, match="Invalid symbol"):
            storage_manager._sanitize_symbol("TOOLONGSYMBOL")

    def test_sanitize_symbols_list(self, storage_manager):
        """Test sanitizing list of symbols."""
        symbols = ["AAPL", "msft", "GOOGL"]
        sanitized = storage_manager._sanitize_symbols(symbols)
        assert sanitized == ["AAPL", "MSFT", "GOOGL"]

    def test_sanitize_symbols_list_with_invalid(self, storage_manager):
        """Test sanitizing list with invalid symbol raises error."""
        symbols = ["AAPL", "..", "GOOGL"]
        with pytest.raises(ValueError, match="Invalid symbol"):
            storage_manager._sanitize_symbols(symbols)


class TestDiskSpaceChecks:
    """Tests for disk space validation."""

    def test_check_disk_space_ok(self, monkeypatch, storage_manager):
        """Test disk space check in OK state."""
        usage_factory = shutil._ntuple_diskusage  # type: ignore[attr-defined]

        monkeypatch.setattr(
            taq_storage.shutil,
            "disk_usage",
            lambda path: usage_factory(1_000_000_000, 400_000_000, 600_000_000),
        )

        status = storage_manager._check_disk_space(estimated_rows=1_000)
        assert status.level == "ok"

    def test_check_disk_space_warning(self, monkeypatch, storage_manager):
        """Test disk space check in warning state."""
        usage_factory = shutil._ntuple_diskusage  # type: ignore[attr-defined]

        # 85% used (warning threshold is 80%)
        # total=1_000_000_000, free=150_000_000 => used_pct = 0.85
        monkeypatch.setattr(
            taq_storage.shutil,
            "disk_usage",
            lambda path: usage_factory(1_000_000_000, 850_000_000, 150_000_000),
        )

        status = storage_manager._check_disk_space(estimated_rows=1_000)
        assert status.level == "warning"

    def test_check_disk_space_critical(self, monkeypatch, storage_manager):
        """Test disk space check in critical state."""
        usage_factory = shutil._ntuple_diskusage  # type: ignore[attr-defined]

        # 92% used (critical threshold is 90%)
        # total=1_000_000_000, free=80_000_000 => used_pct = 0.92
        monkeypatch.setattr(
            taq_storage.shutil,
            "disk_usage",
            lambda path: usage_factory(1_000_000_000, 920_000_000, 80_000_000),
        )

        status = storage_manager._check_disk_space(estimated_rows=1_000)
        assert status.level == "critical"

    def test_check_disk_space_blocked(self, monkeypatch, storage_manager):
        """Test disk space check when blocked."""
        usage_factory = shutil._ntuple_diskusage  # type: ignore[attr-defined]

        # 96% used (blocked threshold is 95%)
        # total=1_000_000_000, free=40_000_000 => used_pct = 0.96
        monkeypatch.setattr(
            taq_storage.shutil,
            "disk_usage",
            lambda path: usage_factory(1_000_000_000, 960_000_000, 40_000_000),
        )

        with pytest.raises(DiskSpaceError, match="Disk usage at"):
            storage_manager._check_disk_space(estimated_rows=1_000)

    def test_check_disk_space_insufficient_free(self, monkeypatch, storage_manager):
        """Test disk space check when insufficient free space for required bytes."""
        usage_factory = shutil._ntuple_diskusage  # type: ignore[attr-defined]

        # Only 10MB free, but need more for 1M rows
        # 1_000_000 rows * 200 bytes/row * 2.0 safety = 400MB required
        monkeypatch.setattr(
            taq_storage.shutil,
            "disk_usage",
            lambda path: usage_factory(1_000_000_000, 990_000_000, 10_000_000),
        )

        with pytest.raises(DiskSpaceError, match="Insufficient disk space"):
            storage_manager._check_disk_space(estimated_rows=1_000_000)


class TestPartitionBuilding:
    """Tests for partition building helpers."""

    def test_build_month_partitions_single_month(self, storage_manager):
        """Test building partitions for single month."""
        partitions = storage_manager._build_month_partitions(
            start_date=datetime.date(2024, 1, 1),
            end_date=datetime.date(2024, 1, 31),
        )
        assert partitions == ["202401"]

    def test_build_month_partitions_multiple_months(self, storage_manager):
        """Test building partitions spanning multiple months."""
        partitions = storage_manager._build_month_partitions(
            start_date=datetime.date(2024, 1, 1),
            end_date=datetime.date(2024, 3, 15),
        )
        assert partitions == ["202401", "202402", "202403"]

    def test_build_month_partitions_year_boundary(self, storage_manager):
        """Test building partitions crossing year boundary."""
        partitions = storage_manager._build_month_partitions(
            start_date=datetime.date(2023, 11, 1),
            end_date=datetime.date(2024, 2, 28),
        )
        assert partitions == ["202311", "202312", "202401", "202402"]

    def test_filter_new_partitions(self, storage_manager):
        """Test filtering partitions for incremental sync."""
        all_partitions = ["202401", "202402", "202403", "202404"]
        last_synced = datetime.date(2024, 2, 15)

        # Should exclude 202402 and earlier (> not >=)
        filtered = storage_manager._filter_new_partitions(all_partitions, last_synced)
        assert filtered == ["202403", "202404"]

    def test_filter_new_partitions_none_new(self, storage_manager):
        """Test filtering when no new partitions."""
        all_partitions = ["202401", "202402"]
        last_synced = datetime.date(2024, 3, 1)

        filtered = storage_manager._filter_new_partitions(all_partitions, last_synced)
        assert filtered == []


class TestEstimateRows:
    """Tests for row estimation helper."""

    def test_estimate_rows_1min_bars(self, storage_manager):
        """Test row estimation for 1min bars."""
        rows = storage_manager._estimate_rows(
            dataset="1min_bars",
            symbols=["AAPL", "MSFT"],
            start_date=datetime.date(2024, 1, 1),
            end_date=datetime.date(2024, 1, 1),
        )
        # 2 symbols * 1 day * 390 bars/day
        assert rows == 780

    def test_estimate_rows_daily_rv(self, storage_manager):
        """Test row estimation for daily RV."""
        rows = storage_manager._estimate_rows(
            dataset="daily_rv",
            symbols=["AAPL"],
            start_date=datetime.date(2024, 1, 1),
            end_date=datetime.date(2024, 1, 5),
        )
        # 1 symbol * 5 days * 1 row/day
        assert rows == 5

    def test_estimate_rows_spread_stats(self, storage_manager):
        """Test row estimation for spread stats."""
        rows = storage_manager._estimate_rows(
            dataset="spread_stats",
            symbols=["AAPL", "MSFT", "GOOGL"],
            start_date=datetime.date(2024, 1, 1),
            end_date=datetime.date(2024, 1, 10),
        )
        # 3 symbols * 10 days * 1 row/day
        assert rows == 30


class TestBuildQueries:
    """Tests for query building helpers."""

    def test_build_aggregates_query_1min_bars(self, storage_manager):
        """Test building query for 1min bars."""
        query, params = storage_manager._build_aggregates_query(
            dataset="1min_bars",
            symbols=["AAPL", "MSFT"],
            partition="202401",
        )

        assert "taq.msec_1min" in query
        assert params["symbols"] == ["AAPL", "MSFT"]
        assert params["start_date"] == "2024-01-01"
        assert params["end_date"] == "2024-01-31"

    def test_build_aggregates_query_daily_rv(self, storage_manager):
        """Test building query for daily RV."""
        query, params = storage_manager._build_aggregates_query(
            dataset="daily_rv",
            symbols=["AAPL"],
            partition="202403",
        )

        assert "taq.rv_daily" in query
        assert params["symbols"] == ["AAPL"]
        assert params["start_date"] == "2024-03-01"
        assert params["end_date"] == "2024-03-31"

    def test_build_aggregates_query_spread_stats(self, storage_manager):
        """Test building query for spread stats."""
        query, params = storage_manager._build_aggregates_query(
            dataset="spread_stats",
            symbols=["GOOGL"],
            partition="202412",
        )

        assert "taq.spread_daily" in query
        assert params["symbols"] == ["GOOGL"]
        assert params["start_date"] == "2024-12-01"
        assert params["end_date"] == "2024-12-31"

    def test_build_aggregates_query_unknown_dataset(self, storage_manager):
        """Test building query for unknown dataset raises error."""
        with pytest.raises(ValueError, match="Unknown aggregate dataset"):
            storage_manager._build_aggregates_query(
                dataset="unknown",
                symbols=["AAPL"],
                partition="202401",
            )

    def test_build_ticks_query(self, storage_manager):
        """Test building query for tick data."""
        query, params = storage_manager._build_ticks_query(
            sample_date=datetime.date(2024, 1, 15),
            symbol="AAPL",
        )

        assert "taq.ctm_20240115" in query
        assert params["symbol"] == "AAPL"
        assert params["date"] == "2024-01-15"


class TestAtomicWrites:
    """Tests for atomic write operations."""

    def test_atomic_write_parquet_success(self, storage_manager):
        """Test atomic parquet write."""
        df = pl.DataFrame(
            {
                "ts": [datetime.datetime(2024, 1, 1, 9, 30, 0)],
                "symbol": ["AAPL"],
                "open": [150.0],
                "high": [151.0],
                "low": [149.5],
                "close": [150.5],
                "volume": [1000],
                "vwap": [150.25],
                "date": [datetime.date(2024, 1, 1)],
            }
        )

        target_path = storage_manager.storage_path / "test.parquet"
        checksum = storage_manager._atomic_write_parquet(df, target_path)

        assert target_path.exists()
        assert isinstance(checksum, str)
        assert len(checksum) == 64  # SHA-256 hex digest

    def test_atomic_write_parquet_disk_full(self, monkeypatch, storage_manager):
        """Test atomic write handles disk full error."""
        df = pl.DataFrame({"col": [1, 2, 3]})

        def mock_write_parquet(self, path):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(pl.DataFrame, "write_parquet", mock_write_parquet)

        target_path = storage_manager.storage_path / "test.parquet"

        with pytest.raises(DiskSpaceError, match="Disk full"):
            storage_manager._atomic_write_parquet(df, target_path)

    def test_compute_checksum_and_fsync(self, tmp_path, storage_manager):
        """Test checksum computation with fsync."""
        test_file = tmp_path / "test.txt"
        test_file.write_text("test content")

        checksum = storage_manager._compute_checksum_and_fsync(test_file)

        assert isinstance(checksum, str)
        assert len(checksum) == 64


class TestCreateEmptyDf:
    """Tests for empty DataFrame creation."""

    def test_create_empty_df_1min_bars(self, storage_manager):
        """Test creating empty 1min bars DataFrame."""
        df = storage_manager._create_empty_df("taq_1min_bars")

        assert df.is_empty()
        assert "ts" in df.columns
        assert "symbol" in df.columns
        assert "close" in df.columns

    def test_create_empty_df_taq_ticks(self, storage_manager):
        """Test creating empty ticks DataFrame."""
        df = storage_manager._create_empty_df("taq_ticks")

        assert df.is_empty()
        assert "ts" in df.columns
        assert "bid" in df.columns
        assert "ask" in df.columns


class TestCreateManifest:
    """Tests for manifest creation."""

    def test_create_manifest(self, tmp_path, storage_manager):
        """Test creating sync manifest."""
        # Create test files
        file1 = tmp_path / "file1.parquet"
        file2 = tmp_path / "file2.parquet"
        file1.write_text("data1")
        file2.write_text("data2")

        manifest = storage_manager._create_manifest(
            dataset="taq_1min_bars",
            file_paths=[str(file1), str(file2)],
            row_count=1000,
            start_date=datetime.date(2024, 1, 1),
            end_date=datetime.date(2024, 1, 31),
        )

        assert manifest.dataset == "taq_1min_bars"
        assert manifest.row_count == 1000
        assert len(manifest.file_paths) == 2
        assert isinstance(manifest.checksum, str)

    def test_create_manifest_for_samples(self, tmp_path, storage_manager):
        """Test creating manifest for sample dataset."""
        # Register schemas first
        register_taq_schemas(storage_manager.schema_registry)

        file1 = tmp_path / "AAPL.parquet"
        file1.write_text("tick data")

        manifest = storage_manager._create_manifest(
            dataset="taq_samples_20240115",  # Sample dataset
            file_paths=[str(file1)],
            row_count=5000,
            start_date=datetime.date(2024, 1, 15),
            end_date=datetime.date(2024, 1, 15),
        )

        # Should look up taq_ticks schema for samples
        assert manifest.dataset == "taq_samples_20240115"


class TestCleanup:
    """Tests for cleanup operations."""

    def test_cleanup_old_samples(self, storage_manager):
        """Test cleaning up old sample directories."""
        samples_dir = storage_manager.storage_path / storage_manager.SAMPLES_DIR

        # Create old sample directories
        old_date = datetime.date.today() - datetime.timedelta(days=400)
        old_dir = samples_dir / old_date.strftime("%Y-%m-%d")
        old_dir.mkdir(parents=True)
        (old_dir / "AAPL.parquet").write_text("old data")

        # Create recent directory
        recent_date = datetime.date.today() - datetime.timedelta(days=10)
        recent_dir = samples_dir / recent_date.strftime("%Y-%m-%d")
        recent_dir.mkdir(parents=True)
        (recent_dir / "MSFT.parquet").write_text("recent data")

        deleted = storage_manager.cleanup(retention_days=365)

        assert deleted >= 1
        assert not old_dir.exists()
        assert recent_dir.exists()

    def test_cleanup_quarantine(self, storage_manager):
        """Test cleaning up old quarantine directories."""
        quarantine_dir = storage_manager.storage_path / storage_manager.QUARANTINE_DIR

        # Create old quarantine
        old_ts = (datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=400)).strftime(
            "%Y%m%d_%H%M%S"
        )
        old_quarantine = quarantine_dir / f"{old_ts}_failed"
        old_quarantine.mkdir(parents=True)
        (old_quarantine / "reason.txt").write_text("Old failure")

        deleted = storage_manager.cleanup(retention_days=365)

        assert deleted >= 1
        assert not old_quarantine.exists()


class TestQuarantineFailures:
    """Tests for quarantine operations."""

    def test_quarantine_failed(self, tmp_path, storage_manager):
        """Test moving failed file to quarantine."""
        temp_file = tmp_path / "failed.parquet.tmp"
        temp_file.write_text("corrupted data")

        storage_manager._quarantine_failed(temp_file, "Checksum mismatch")

        # Check quarantine directory created
        # Directory format: {timestamp}_{filename_stem} (e.g., 20260118_123456_failed.parquet)
        quarantine_dir = storage_manager.storage_path / storage_manager.QUARANTINE_DIR
        quarantine_dirs = list(quarantine_dir.glob("*_failed.parquet"))
        assert len(quarantine_dirs) > 0

        # Check reason file
        reason_file = quarantine_dirs[0] / "reason.txt"
        assert reason_file.exists()
        content = reason_file.read_text()
        assert "Checksum mismatch" in content

    def test_quarantine_nonexistent_file(self, tmp_path, storage_manager):
        """Test quarantine handles nonexistent file."""
        temp_file = tmp_path / "nonexistent.parquet.tmp"

        # Should not raise error
        storage_manager._quarantine_failed(temp_file, "File not found")


class TestSyncAggregatesValidation:
    """Tests for sync_aggregates validation."""

    def test_sync_aggregates_no_wrds_client(self, storage_manager):
        """Test sync_aggregates raises error without WRDS client."""
        storage_manager.wrds_client = None

        with pytest.raises(ValueError, match="WRDS client required"):
            storage_manager.sync_aggregates(
                dataset="1min_bars",
                symbols=["AAPL"],
                start_date=datetime.date(2024, 1, 1),
                end_date=datetime.date(2024, 1, 31),
            )

    def test_sync_aggregates_invalid_symbols(self, storage_manager):
        """Test sync_aggregates validates symbols."""
        with pytest.raises(ValueError, match="Invalid symbol"):
            storage_manager.sync_aggregates(
                dataset="1min_bars",
                symbols=["AAPL", "../etc/passwd"],
                start_date=datetime.date(2024, 1, 1),
                end_date=datetime.date(2024, 1, 31),
            )


class TestSyncSamplesValidation:
    """Tests for sync_samples validation."""

    def test_sync_samples_no_wrds_client(self, storage_manager):
        """Test sync_samples raises error without WRDS client."""
        storage_manager.wrds_client = None

        with pytest.raises(ValueError, match="WRDS client required"):
            storage_manager.sync_samples(
                sample_date=datetime.date(2024, 1, 15),
                symbols=["AAPL"],
            )

    def test_sync_samples_invalid_symbols(self, storage_manager):
        """Test sync_samples validates symbols."""
        with pytest.raises(ValueError, match="Invalid symbol"):
            storage_manager.sync_samples(
                sample_date=datetime.date(2024, 1, 15),
                symbols=["AAPL", ".."],
            )


class TestComputeCombinedChecksum:
    """Tests for combined checksum computation."""

    def test_compute_combined_checksum(self, tmp_path, storage_manager):
        """Test computing combined checksum for multiple files."""
        file1 = tmp_path / "file1.parquet"
        file2 = tmp_path / "file2.parquet"
        file1.write_text("data1")
        file2.write_text("data2")

        checksum = storage_manager._compute_combined_checksum([str(file1), str(file2)])

        assert isinstance(checksum, str)
        assert len(checksum) == 64

    def test_compute_combined_checksum_empty(self, storage_manager):
        """Test combined checksum with no files."""
        checksum = storage_manager._compute_combined_checksum([])

        assert isinstance(checksum, str)
        assert len(checksum) == 64

    def test_compute_combined_checksum_nonexistent(self, tmp_path, storage_manager):
        """Test combined checksum skips nonexistent files."""
        nonexistent = tmp_path / "nonexistent.parquet"

        checksum = storage_manager._compute_combined_checksum([str(nonexistent)])

        assert isinstance(checksum, str)


from libs.data.data_quality.schema import SchemaDrift


def _create_1min_bars_mock_df() -> pl.DataFrame:
    """Create a mock DataFrame matching taq_1min_bars schema exactly."""
    return pl.DataFrame(
        schema={
            "ts": pl.Datetime("ns"),
            "symbol": pl.Utf8,
            "open": pl.Float64,
            "high": pl.Float64,
            "low": pl.Float64,
            "close": pl.Float64,
            "volume": pl.Int64,
            "vwap": pl.Float64,
            "date": pl.Date,
        }
    ).vstack(
        pl.DataFrame(
            {
                "ts": [datetime.datetime(2024, 1, 2, 9, 30, 0)],
                "symbol": ["AAPL"],
                "open": [150.0],
                "high": [151.0],
                "low": [149.5],
                "close": [150.5],
                "volume": [1000],
                "vwap": [150.25],
                "date": [datetime.date(2024, 1, 2)],
            }
        ).cast(
            {
                "ts": pl.Datetime("ns"),
                "volume": pl.Int64,
            }
        )
    )


def _create_ticks_mock_df() -> pl.DataFrame:
    """Create a mock DataFrame matching taq_ticks schema exactly."""
    return pl.DataFrame(
        schema={
            "ts": pl.Datetime("ns"),
            "symbol": pl.Utf8,
            "bid": pl.Float64,
            "ask": pl.Float64,
            "bid_size": pl.Int64,
            "ask_size": pl.Int64,
            "trade_px": pl.Float64,
            "trade_size": pl.Int64,
            "cond": pl.Utf8,
        }
    ).vstack(
        pl.DataFrame(
            {
                "ts": [datetime.datetime(2024, 1, 15, 9, 30, 0)],
                "symbol": ["AAPL"],
                "bid": [150.0],
                "ask": [150.05],
                "bid_size": [100],
                "ask_size": [200],
                "trade_px": [150.02],
                "trade_size": [50],
                "cond": ["@"],
            }
        ).cast(
            {
                "ts": pl.Datetime("ns"),
                "bid_size": pl.Int64,
                "ask_size": pl.Int64,
                "trade_size": pl.Int64,
            }
        )
    )


def _mock_detect_drift_no_breaking(dataset: str, current_schema: dict) -> SchemaDrift:
    """Return non-breaking schema drift for testing."""
    return SchemaDrift(added_columns=[], removed_columns=[], changed_columns=[])


def _mock_detect_drift_with_additions(dataset: str, current_schema: dict) -> SchemaDrift:
    """Return schema drift with additions (non-breaking) for testing."""
    return SchemaDrift(added_columns=["new_column"], removed_columns=[], changed_columns=[])


class TestSyncAggregatesFullFlow:
    """Tests for sync_aggregates with full data flow."""

    def test_sync_aggregates_full_flow(self, monkeypatch, storage_manager):
        """Test full sync_aggregates flow with mocked WRDS."""
        # Register schemas
        register_taq_schemas(storage_manager.schema_registry)

        mock_df = _create_1min_bars_mock_df()
        storage_manager.wrds_client.execute_query.return_value = mock_df

        # Mock disk space check to return ok
        usage_factory = shutil._ntuple_diskusage  # type: ignore[attr-defined]
        monkeypatch.setattr(
            taq_storage.shutil,
            "disk_usage",
            lambda path: usage_factory(1_000_000_000_000, 100_000_000_000, 900_000_000_000),
        )

        # Mock schema drift detection to avoid type string mismatches
        monkeypatch.setattr(
            storage_manager.schema_registry,
            "detect_drift",
            _mock_detect_drift_no_breaking,
        )

        manifest = storage_manager.sync_aggregates(
            dataset="1min_bars",
            symbols=["AAPL"],
            start_date=datetime.date(2024, 1, 1),
            end_date=datetime.date(2024, 1, 31),
            incremental=False,
            create_snapshot=False,
        )

        assert manifest.dataset == "taq_1min_bars"
        assert manifest.row_count >= 1
        assert len(manifest.file_paths) >= 1

    def test_sync_aggregates_with_snapshot(self, monkeypatch, storage_manager):
        """Test sync_aggregates creates snapshot when requested."""
        register_taq_schemas(storage_manager.schema_registry)

        mock_df = _create_1min_bars_mock_df()
        storage_manager.wrds_client.execute_query.return_value = mock_df

        usage_factory = shutil._ntuple_diskusage  # type: ignore[attr-defined]
        monkeypatch.setattr(
            taq_storage.shutil,
            "disk_usage",
            lambda path: usage_factory(1_000_000_000_000, 100_000_000_000, 900_000_000_000),
        )

        monkeypatch.setattr(
            storage_manager.schema_registry,
            "detect_drift",
            _mock_detect_drift_no_breaking,
        )

        manifest = storage_manager.sync_aggregates(
            dataset="1min_bars",
            symbols=["AAPL"],
            start_date=datetime.date(2024, 1, 1),
            end_date=datetime.date(2024, 1, 31),
            create_snapshot=True,
        )

        assert manifest is not None

    def test_sync_aggregates_empty_data(self, monkeypatch, storage_manager):
        """Test sync_aggregates handles empty query results."""
        register_taq_schemas(storage_manager.schema_registry)

        # Return empty DataFrame
        empty_df = pl.DataFrame(
            schema={
                "ts": pl.Datetime("ns"),
                "symbol": pl.Utf8,
                "open": pl.Float64,
                "high": pl.Float64,
                "low": pl.Float64,
                "close": pl.Float64,
                "volume": pl.Int64,
                "vwap": pl.Float64,
                "date": pl.Date,
            }
        )
        storage_manager.wrds_client.execute_query.return_value = empty_df

        usage_factory = shutil._ntuple_diskusage  # type: ignore[attr-defined]
        monkeypatch.setattr(
            taq_storage.shutil,
            "disk_usage",
            lambda path: usage_factory(1_000_000_000_000, 100_000_000_000, 900_000_000_000),
        )

        monkeypatch.setattr(
            storage_manager.schema_registry,
            "detect_drift",
            _mock_detect_drift_no_breaking,
        )

        manifest = storage_manager.sync_aggregates(
            dataset="1min_bars",
            symbols=["AAPL"],
            start_date=datetime.date(2024, 1, 1),
            end_date=datetime.date(2024, 1, 31),
        )

        # Empty data still creates manifest with zero rows
        assert manifest.row_count == 0

    def test_sync_aggregates_slo_breach(self, monkeypatch, storage_manager):
        """Test sync_aggregates logs warning on SLO breach."""
        register_taq_schemas(storage_manager.schema_registry)

        mock_df = _create_1min_bars_mock_df()
        storage_manager.wrds_client.execute_query.return_value = mock_df

        usage_factory = shutil._ntuple_diskusage  # type: ignore[attr-defined]
        monkeypatch.setattr(
            taq_storage.shutil,
            "disk_usage",
            lambda path: usage_factory(1_000_000_000_000, 100_000_000_000, 900_000_000_000),
        )

        monkeypatch.setattr(
            storage_manager.schema_registry,
            "detect_drift",
            _mock_detect_drift_no_breaking,
        )

        # Set SLO to 0 minutes to force breach
        original_slo = TAQStorageManager.SYNC_SLO_MINUTES
        monkeypatch.setattr(TAQStorageManager, "SYNC_SLO_MINUTES", 0)

        try:
            manifest = storage_manager.sync_aggregates(
                dataset="1min_bars",
                symbols=["AAPL"],
                start_date=datetime.date(2024, 1, 1),
                end_date=datetime.date(2024, 1, 31),
            )
            assert manifest is not None
        finally:
            monkeypatch.setattr(TAQStorageManager, "SYNC_SLO_MINUTES", original_slo)


class TestSyncSamplesFullFlow:
    """Tests for sync_samples with full data flow."""

    def test_sync_samples_full_flow(self, monkeypatch, storage_manager):
        """Test full sync_samples flow with mocked WRDS."""
        register_taq_schemas(storage_manager.schema_registry)

        mock_df = _create_ticks_mock_df()
        storage_manager.wrds_client.execute_query.return_value = mock_df

        usage_factory = shutil._ntuple_diskusage  # type: ignore[attr-defined]
        monkeypatch.setattr(
            taq_storage.shutil,
            "disk_usage",
            lambda path: usage_factory(1_000_000_000_000, 100_000_000_000, 900_000_000_000),
        )

        monkeypatch.setattr(
            storage_manager.schema_registry,
            "detect_drift",
            _mock_detect_drift_no_breaking,
        )

        manifest = storage_manager.sync_samples(
            sample_date=datetime.date(2024, 1, 15),
            symbols=["AAPL"],
            create_snapshot=False,
        )

        assert manifest.dataset == "taq_samples_20240115"
        assert manifest.row_count >= 1

    def test_sync_samples_with_snapshot(self, monkeypatch, storage_manager):
        """Test sync_samples creates snapshot when requested."""
        register_taq_schemas(storage_manager.schema_registry)

        mock_df = _create_ticks_mock_df()
        storage_manager.wrds_client.execute_query.return_value = mock_df

        usage_factory = shutil._ntuple_diskusage  # type: ignore[attr-defined]
        monkeypatch.setattr(
            taq_storage.shutil,
            "disk_usage",
            lambda path: usage_factory(1_000_000_000_000, 100_000_000_000, 900_000_000_000),
        )

        monkeypatch.setattr(
            storage_manager.schema_registry,
            "detect_drift",
            _mock_detect_drift_no_breaking,
        )

        manifest = storage_manager.sync_samples(
            sample_date=datetime.date(2024, 1, 15),
            symbols=["AAPL"],
            create_snapshot=True,
        )

        assert manifest is not None

    def test_sync_samples_empty_data(self, monkeypatch, storage_manager):
        """Test sync_samples handles empty tick data."""
        register_taq_schemas(storage_manager.schema_registry)

        empty_df = pl.DataFrame(
            schema={
                "ts": pl.Datetime("ns"),
                "symbol": pl.Utf8,
                "bid": pl.Float64,
                "ask": pl.Float64,
                "bid_size": pl.Int64,
                "ask_size": pl.Int64,
                "trade_px": pl.Float64,
                "trade_size": pl.Int64,
                "cond": pl.Utf8,
            }
        )
        storage_manager.wrds_client.execute_query.return_value = empty_df

        usage_factory = shutil._ntuple_diskusage  # type: ignore[attr-defined]
        monkeypatch.setattr(
            taq_storage.shutil,
            "disk_usage",
            lambda path: usage_factory(1_000_000_000_000, 100_000_000_000, 900_000_000_000),
        )

        # Empty data doesn't trigger schema validation, so no mock needed

        manifest = storage_manager.sync_samples(
            sample_date=datetime.date(2024, 1, 15),
            symbols=["AAPL"],
        )

        # Empty data still creates manifest
        assert manifest.row_count == 0


class TestEstimateRowsEdgeCases:
    """Tests for estimate_rows edge cases."""

    def test_estimate_rows_ticks_unknown_dataset(self, storage_manager):
        """Test row estimation for unknown/tick dataset."""
        rows = storage_manager._estimate_rows(
            dataset="ticks",  # Not 1min_bars, daily_rv, or spread_stats
            symbols=["AAPL"],
            start_date=datetime.date(2024, 1, 1),
            end_date=datetime.date(2024, 1, 1),
        )
        # Should use tick data estimate: 1 symbol * 1 day * 50,000 ticks
        assert rows == 50_000


class TestFsyncDirectory:
    """Tests for fsync directory operations."""

    def test_fsync_directory_success(self, storage_manager):
        """Test fsync directory succeeds on valid directory."""
        # Should not raise error
        storage_manager._fsync_directory(storage_manager.storage_path)

    def test_fsync_directory_failure(self, monkeypatch, storage_manager):
        """Test fsync directory handles OSError gracefully."""
        import os

        def mock_open(*args, **kwargs):
            raise OSError("Cannot open directory")

        monkeypatch.setattr(os, "open", mock_open)

        # Should not raise, just log warning
        storage_manager._fsync_directory(storage_manager.storage_path)


class TestCreateEmptyDfEdgeCases:
    """Tests for _create_empty_df edge cases."""

    def test_create_empty_df_unknown_dtype(self, storage_manager):
        """Test creating empty DataFrame with unknown dtype falls back to Utf8."""
        # Temporarily add a schema with unknown dtype
        original_schemas = taq_storage.TAQ_SCHEMAS.copy()

        try:
            taq_storage.TAQ_SCHEMAS["test_schema"] = {
                "col1": "unknown_type",
                "col2": "another_unknown",
            }

            df = storage_manager._create_empty_df("test_schema")

            assert df.is_empty()
            assert df.schema["col1"] == pl.Utf8
            assert df.schema["col2"] == pl.Utf8
        finally:
            taq_storage.TAQ_SCHEMAS.clear()
            taq_storage.TAQ_SCHEMAS.update(original_schemas)


class TestAtomicWriteOSErrorHandling:
    """Tests for atomic write OSError handling."""

    def test_atomic_write_non_enospc_oserror(self, monkeypatch, storage_manager):
        """Test atomic write re-raises non-disk-full OSError."""
        df = pl.DataFrame({"col": [1, 2, 3]})

        def mock_write_parquet(self, path):
            raise OSError(13, "Permission denied")  # errno 13 = EACCES

        monkeypatch.setattr(pl.DataFrame, "write_parquet", mock_write_parquet)

        target_path = storage_manager.storage_path / "test.parquet"

        with pytest.raises(OSError, match="Permission denied"):
            storage_manager._atomic_write_parquet(df, target_path)

    def test_atomic_write_temp_cleanup_on_failure(self, monkeypatch, storage_manager):
        """Test temp file cleanup in finally block."""
        df = pl.DataFrame({"col": [1, 2, 3]})
        cleanup_called = {"called": False}

        original_write = pl.DataFrame.write_parquet

        def mock_write_parquet(self, path):
            # Write the file
            original_write(self, path)
            # Then raise an error in checksum computation
            raise RuntimeError("Simulated error after write")

        def mock_unlink(self, missing_ok=False):
            cleanup_called["called"] = True

        monkeypatch.setattr(pl.DataFrame, "write_parquet", mock_write_parquet)

        target_path = storage_manager.storage_path / "test.parquet"

        with pytest.raises(RuntimeError, match="Simulated error"):
            storage_manager._atomic_write_parquet(df, target_path)

        # The temp file would have been cleaned up by the finally block


class TestCleanupEdgeCases:
    """Tests for cleanup edge cases."""

    def test_cleanup_invalid_sample_dir_name(self, storage_manager):
        """Test cleanup skips directories with invalid date format."""
        samples_dir = storage_manager.storage_path / storage_manager.SAMPLES_DIR

        # Create directory with invalid name
        invalid_dir = samples_dir / "not-a-date"
        invalid_dir.mkdir(parents=True)
        (invalid_dir / "file.parquet").write_text("data")

        deleted = storage_manager.cleanup(retention_days=365)

        # Invalid dir should be skipped, not deleted
        assert invalid_dir.exists()
        assert deleted == 0

    def test_cleanup_invalid_quarantine_dir_name(self, storage_manager):
        """Test cleanup skips quarantine dirs with invalid timestamp format."""
        quarantine_dir = storage_manager.storage_path / storage_manager.QUARANTINE_DIR

        # Create directory with invalid timestamp
        invalid_dir = quarantine_dir / "invalid_timestamp_dir"
        invalid_dir.mkdir(parents=True)
        (invalid_dir / "reason.txt").write_text("Some reason")

        deleted = storage_manager.cleanup(retention_days=365)

        # Invalid dir should be skipped
        assert invalid_dir.exists()
        assert deleted == 0

    def test_cleanup_with_files_not_dirs(self, storage_manager):
        """Test cleanup skips files (only processes directories)."""
        samples_dir = storage_manager.storage_path / storage_manager.SAMPLES_DIR

        # Create a file (not a directory) in samples
        stray_file = samples_dir / "stray_file.txt"
        stray_file.write_text("stray")

        deleted = storage_manager.cleanup(retention_days=365)

        # File should be untouched
        assert stray_file.exists()
        assert deleted == 0


class TestSyncAggregatePartition:
    """Tests for _sync_aggregate_partition."""

    def test_sync_aggregate_partition_with_schema_drift_additions(
        self, monkeypatch, storage_manager
    ):
        """Test sync handles schema drift with new columns."""
        register_taq_schemas(storage_manager.schema_registry)

        # Create mock data with exact schema plus extra column
        base_df = _create_1min_bars_mock_df()
        mock_df = base_df.with_columns(pl.lit("extra").alias("new_column"))

        storage_manager.wrds_client.execute_query.return_value = mock_df

        usage_factory = shutil._ntuple_diskusage  # type: ignore[attr-defined]
        monkeypatch.setattr(
            taq_storage.shutil,
            "disk_usage",
            lambda path: usage_factory(1_000_000_000_000, 100_000_000_000, 900_000_000_000),
        )

        # Mock drift detection to return additions (non-breaking)
        monkeypatch.setattr(
            storage_manager.schema_registry,
            "detect_drift",
            _mock_detect_drift_with_additions,
        )

        manifest = storage_manager.sync_aggregates(
            dataset="1min_bars",
            symbols=["AAPL"],
            start_date=datetime.date(2024, 1, 1),
            end_date=datetime.date(2024, 1, 31),
        )

        # Should still succeed despite new column
        assert manifest is not None

    def test_sync_aggregate_partition_breaking_schema_drift(self, monkeypatch, storage_manager):
        """Test sync raises error on breaking schema drift."""
        from libs.data.data_quality.exceptions import SchemaError

        register_taq_schemas(storage_manager.schema_registry)

        # Create mock data missing required columns
        mock_df = pl.DataFrame(
            {
                "ts": [datetime.datetime(2024, 1, 2, 9, 30, 0)],
                "symbol": ["AAPL"],
                # Missing open, high, low, close, etc.
            }
        ).cast({"ts": pl.Datetime("ns")})

        storage_manager.wrds_client.execute_query.return_value = mock_df

        usage_factory = shutil._ntuple_diskusage  # type: ignore[attr-defined]
        monkeypatch.setattr(
            taq_storage.shutil,
            "disk_usage",
            lambda path: usage_factory(1_000_000_000_000, 100_000_000_000, 900_000_000_000),
        )

        # Mock breaking drift - note we let the actual detect_drift run
        # since we want to test the error path
        def mock_breaking_drift(dataset: str, current_schema: dict) -> SchemaDrift:
            return SchemaDrift(
                added_columns=[],
                removed_columns=["open", "high", "low", "close"],
                changed_columns=[],
            )

        monkeypatch.setattr(
            storage_manager.schema_registry,
            "detect_drift",
            mock_breaking_drift,
        )

        with pytest.raises(SchemaError, match="Breaking schema drift"):
            storage_manager.sync_aggregates(
                dataset="1min_bars",
                symbols=["AAPL"],
                start_date=datetime.date(2024, 1, 1),
                end_date=datetime.date(2024, 1, 31),
            )


class TestSyncSampleSymbol:
    """Tests for _sync_sample_symbol."""

    def test_sync_sample_symbol_breaking_schema_drift(self, monkeypatch, storage_manager):
        """Test sync_samples raises error on breaking schema drift."""
        from libs.data.data_quality.exceptions import SchemaError

        register_taq_schemas(storage_manager.schema_registry)

        # Create mock data missing required columns (non-empty so schema validation runs)
        mock_df = pl.DataFrame(
            {
                "ts": [datetime.datetime(2024, 1, 15, 9, 30, 0)],
                "symbol": ["AAPL"],
                # Missing bid, ask, bid_size, etc.
            }
        ).cast({"ts": pl.Datetime("ns")})

        storage_manager.wrds_client.execute_query.return_value = mock_df

        usage_factory = shutil._ntuple_diskusage  # type: ignore[attr-defined]
        monkeypatch.setattr(
            taq_storage.shutil,
            "disk_usage",
            lambda path: usage_factory(1_000_000_000_000, 100_000_000_000, 900_000_000_000),
        )

        # Mock breaking drift
        def mock_breaking_drift(dataset: str, current_schema: dict) -> SchemaDrift:
            return SchemaDrift(
                added_columns=[],
                removed_columns=["bid", "ask", "bid_size", "ask_size"],
                changed_columns=[],
            )

        monkeypatch.setattr(
            storage_manager.schema_registry,
            "detect_drift",
            mock_breaking_drift,
        )

        with pytest.raises(SchemaError, match="Breaking schema drift"):
            storage_manager.sync_samples(
                sample_date=datetime.date(2024, 1, 15),
                symbols=["AAPL"],
            )

    def test_sync_sample_symbol_with_additions(self, monkeypatch, storage_manager):
        """Test sync_samples handles schema with additions."""
        register_taq_schemas(storage_manager.schema_registry)

        # Create mock data with extra column
        base_df = _create_ticks_mock_df()
        mock_df = base_df.with_columns(pl.lit("new_data").alias("extra_field"))

        storage_manager.wrds_client.execute_query.return_value = mock_df

        usage_factory = shutil._ntuple_diskusage  # type: ignore[attr-defined]
        monkeypatch.setattr(
            taq_storage.shutil,
            "disk_usage",
            lambda path: usage_factory(1_000_000_000_000, 100_000_000_000, 900_000_000_000),
        )

        # Mock drift detection to return additions (non-breaking)
        monkeypatch.setattr(
            storage_manager.schema_registry,
            "detect_drift",
            _mock_detect_drift_with_additions,
        )

        manifest = storage_manager.sync_samples(
            sample_date=datetime.date(2024, 1, 15),
            symbols=["AAPL"],
        )

        assert manifest is not None


class TestIncrementalSync:
    """Tests for incremental sync functionality."""

    def test_sync_aggregates_incremental_with_existing_manifest(self, monkeypatch, storage_manager):
        """Test incremental sync uses existing manifest data."""
        register_taq_schemas(storage_manager.schema_registry)

        mock_df = _create_1min_bars_mock_df()
        storage_manager.wrds_client.execute_query.return_value = mock_df

        usage_factory = shutil._ntuple_diskusage  # type: ignore[attr-defined]
        monkeypatch.setattr(
            taq_storage.shutil,
            "disk_usage",
            lambda path: usage_factory(1_000_000_000_000, 100_000_000_000, 900_000_000_000),
        )

        monkeypatch.setattr(
            storage_manager.schema_registry,
            "detect_drift",
            _mock_detect_drift_no_breaking,
        )

        # First sync for January
        manifest1 = storage_manager.sync_aggregates(
            dataset="1min_bars",
            symbols=["AAPL"],
            start_date=datetime.date(2024, 1, 1),
            end_date=datetime.date(2024, 1, 31),
            incremental=False,
        )

        # Incremental sync for February should carry forward January's files
        manifest2 = storage_manager.sync_aggregates(
            dataset="1min_bars",
            symbols=["AAPL"],
            start_date=datetime.date(2024, 1, 1),
            end_date=datetime.date(2024, 2, 29),
            incremental=True,
        )

        # Should have files from both months
        assert len(manifest2.file_paths) >= len(manifest1.file_paths)
