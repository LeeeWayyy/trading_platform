"""Tests for libs.data_quality.versioning module.

Test coverage: 38 test cases organized into 9 categories:
- Core Functionality (5 tests)
- Consistency & Locking (3 tests)
- Time-Travel (6 tests)
- Backtest Linkage (4 tests)
- Storage & CAS (6 tests)
- Checksums & Integrity (3 tests)
- Error Handling & Recovery (4 tests)
- Snapshot Deletion (5 tests)
- Edge Cases (2 tests)
"""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from libs.data.data_quality.exceptions import (
    DataNotFoundError,
    DatasetNotInSnapshotError,
    SnapshotInconsistentError,
    SnapshotNotFoundError,
    SnapshotReferencedError,
)
from libs.data.data_quality.manifest import ManifestManager, SyncManifest
from libs.data.data_quality.types import LockToken
from libs.data.data_quality.versioning import (
    BacktestLinkage,
    DatasetVersionManager,
    FileStorageInfo,
    SnapshotManifest,
)


class TestVersioningFixtures:
    """Shared fixtures for versioning tests."""

    @pytest.fixture()
    def temp_dirs(self, tmp_path: Path) -> dict[str, Path]:
        """Create all required temp directories."""
        dirs = {
            "manifests": tmp_path / "manifests",
            "locks": tmp_path / "locks",
            "backups": tmp_path / "backups",
            "quarantine": tmp_path / "quarantine",
            "snapshots": tmp_path / "snapshots",
            "cas": tmp_path / "cas",
            "diffs": tmp_path / "diffs",
            "backtests": tmp_path / "backtests",
            "data": tmp_path / "data",
        }
        for d in dirs.values():
            d.mkdir(parents=True, exist_ok=True)
        return dirs

    @pytest.fixture()
    def manifest_manager(self, temp_dirs: dict[str, Path], tmp_path: Path) -> ManifestManager:
        """Create ManifestManager with temp directories."""
        return ManifestManager(
            storage_path=temp_dirs["manifests"],
            lock_dir=temp_dirs["locks"],
            backup_dir=temp_dirs["backups"],
            quarantine_dir=temp_dirs["quarantine"],
            data_root=tmp_path,
        )

    @pytest.fixture()
    def version_manager(
        self, manifest_manager: ManifestManager, temp_dirs: dict[str, Path], tmp_path: Path
    ) -> DatasetVersionManager:
        """Create DatasetVersionManager."""
        return DatasetVersionManager(
            manifest_manager=manifest_manager,
            snapshots_dir=temp_dirs["snapshots"],
            cas_dir=temp_dirs["cas"],
            diffs_dir=temp_dirs["diffs"],
            backtests_dir=temp_dirs["backtests"],
            locks_dir=temp_dirs["locks"],
            data_root=tmp_path,
        )

    @pytest.fixture()
    def valid_lock_token(self, temp_dirs: dict[str, Path]) -> LockToken:
        """Create a valid lock token."""
        now = datetime.now(UTC)
        lock_path = temp_dirs["locks"] / "test_dataset.lock"

        token = LockToken(
            pid=os.getpid(),
            hostname="test-host",
            writer_id="test-writer",
            acquired_at=now,
            expires_at=now + timedelta(hours=4),
            lock_path=lock_path,
        )

        with open(lock_path, "w") as f:
            json.dump(token.to_dict(), f)

        return token

    def _create_test_parquet(self, path: Path, content: str = "test data") -> Path:
        """Create a test parquet file (simulated)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return path

    def _create_manifest_with_data(
        self,
        manager: ManifestManager,
        dataset: str,
        temp_dirs: dict[str, Path],
        content: str = "test data",
    ) -> tuple[SyncManifest, Path]:
        """Create a manifest with associated data file."""
        # Create data file
        data_file = temp_dirs["data"] / f"{dataset}.parquet"
        self._create_test_parquet(data_file, content)

        # Create lock
        lock_path = temp_dirs["locks"] / f"{dataset}.lock"
        now = datetime.now(UTC)
        token = LockToken(
            pid=os.getpid(),
            hostname="test-host",
            writer_id="test-writer",
            acquired_at=now,
            expires_at=now + timedelta(hours=4),
            lock_path=lock_path,
        )
        with open(lock_path, "w") as f:
            json.dump(token.to_dict(), f)

        # Create manifest
        manifest = SyncManifest(
            dataset=dataset,
            sync_timestamp=now,
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            row_count=1000,
            checksum="abc123" * 10,
            schema_version="v1.0.0",
            wrds_query_hash="def456" * 10,
            file_paths=[str(data_file)],
            validation_status="passed",
        )
        manager.save_manifest(manifest, token)

        return manifest, data_file


# =============================================================================
# Core Functionality Tests (5 tests)
# =============================================================================


class TestCoreCreateSnapshot(TestVersioningFixtures):
    """Core snapshot creation tests."""

    def test_create_snapshot_basic(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test basic snapshot creation."""
        # Setup: create manifest with data
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)

        # Create snapshot
        snapshot = version_manager.create_snapshot("v1.0.0")

        # Verify
        assert snapshot.version_tag == "v1.0.0"
        assert "crsp_daily" in snapshot.datasets
        assert snapshot.total_size_bytes > 0
        assert snapshot.aggregate_checksum is not None

    def test_create_snapshot_multiple_datasets(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test snapshot with multiple datasets."""
        # Setup: create multiple manifests
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)
        self._create_manifest_with_data(manifest_manager, "compustat", temp_dirs)
        self._create_manifest_with_data(manifest_manager, "fama_french", temp_dirs)

        # Create snapshot
        snapshot = version_manager.create_snapshot("multi-dataset-v1")

        # Verify
        assert len(snapshot.datasets) == 3
        assert "crsp_daily" in snapshot.datasets
        assert "compustat" in snapshot.datasets
        assert "fama_french" in snapshot.datasets

    def test_create_snapshot_selected_datasets(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test snapshot with only selected datasets."""
        # Setup: create multiple manifests
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)
        self._create_manifest_with_data(manifest_manager, "compustat", temp_dirs)

        # Create snapshot with only crsp_daily
        snapshot = version_manager.create_snapshot("selected-v1", datasets=["crsp_daily"])

        # Verify
        assert len(snapshot.datasets) == 1
        assert "crsp_daily" in snapshot.datasets
        assert "compustat" not in snapshot.datasets

    def test_get_snapshot_by_version(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test retrieving snapshot by version tag."""
        # Setup
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)
        version_manager.create_snapshot("v1.0.0")

        # Retrieve
        snapshot = version_manager.get_snapshot("v1.0.0")

        # Verify
        assert snapshot is not None
        assert snapshot.version_tag == "v1.0.0"

    def test_list_snapshots_ordered(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test list_snapshots returns snapshots ordered by creation date."""
        # Setup
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)

        # Create snapshots
        version_manager.create_snapshot("v1.0.0")
        version_manager.create_snapshot("v2.0.0")
        version_manager.create_snapshot("v3.0.0")

        # List
        snapshots = version_manager.list_snapshots()

        # Verify ordered by creation date (newest first)
        assert len(snapshots) == 3
        assert snapshots[0].version_tag == "v3.0.0"
        assert snapshots[1].version_tag == "v2.0.0"
        assert snapshots[2].version_tag == "v1.0.0"


# =============================================================================
# Consistency & Locking Tests (3 tests)
# =============================================================================


class TestConsistencyLocking(TestVersioningFixtures):
    """Tests for optimistic concurrency and locking."""

    def test_snapshot_fails_on_manifest_change(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test snapshot fails if manifest changes during creation."""
        # Setup
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)

        # Patch the _store_file_batched to simulate a modification during snapshot
        original_store = version_manager._store_file_batched
        call_count = [0]

        def mock_store(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # Simulate manifest modification after first file stored
                lock_path = temp_dirs["locks"] / "crsp_daily.lock"
                now = datetime.now(UTC)
                token = LockToken(
                    pid=os.getpid(),
                    hostname="test-host",
                    writer_id="test-writer",
                    acquired_at=now,
                    expires_at=now + timedelta(hours=4),
                    lock_path=lock_path,
                )
                with open(lock_path, "w") as f:
                    json.dump(token.to_dict(), f)

                # Update manifest to increment version
                manifest = manifest_manager.load_manifest("crsp_daily")
                assert manifest is not None  # Should exist
                manifest_manager.save_manifest(manifest, token)

            return original_store(*args, **kwargs)

        with patch.object(version_manager, "_store_file_batched", side_effect=mock_store):
            with pytest.raises(SnapshotInconsistentError) as exc_info:
                version_manager.create_snapshot("conflict-v1")

            assert "crsp_daily" in str(exc_info.value)
            assert "modified during snapshot" in str(exc_info.value)

    def test_duplicate_version_tag_rejected(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test creating snapshot with existing version tag fails."""
        # Setup
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)
        version_manager.create_snapshot("v1.0.0")

        # Try duplicate
        with pytest.raises(ValueError, match="already exists"):
            version_manager.create_snapshot("v1.0.0")

    def test_invalid_version_tag_rejected(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test invalid version tags are rejected."""
        # Setup
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)

        # Test various invalid tags
        invalid_tags = [
            "",
            "v1/2/3",  # Contains slash
            ".hidden",  # Starts with dot
            "../escape",  # Path traversal attempt
            "foo/../bar",  # Path traversal in middle
            "-invalid",  # Starts with non-alphanumeric
            "_also_invalid",  # Starts with underscore
        ]

        for tag in invalid_tags:
            with pytest.raises(ValueError, match="Invalid version"):
                version_manager.create_snapshot(tag)


# =============================================================================
# Time-Travel Tests (6 tests)
# =============================================================================


class TestTimeTravel(TestVersioningFixtures):
    """Tests for time-travel queries."""

    def _backdate_snapshot(
        self, temp_dirs: dict[str, Path], version_tag: str, backdate: date
    ) -> None:
        """Backdate a snapshot's created_at to a specific date."""
        snapshot_path = temp_dirs["snapshots"] / version_tag / "manifest.json"
        with open(snapshot_path) as f:
            data = json.load(f)

        # Set created_at to noon UTC on the target date
        backdated_dt = datetime(backdate.year, backdate.month, backdate.day, 12, 0, 0, tzinfo=UTC)
        data["created_at"] = backdated_dt.isoformat()

        with open(snapshot_path, "w") as f:
            json.dump(data, f)

    def test_query_as_of_returns_latest_before_date(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test query_as_of returns latest snapshot before given date."""
        # Setup
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)

        # Create date-based snapshots and backdate them
        version_manager.create_snapshot("2024-01-01")
        self._backdate_snapshot(temp_dirs, "2024-01-01", date(2024, 1, 1))

        version_manager.create_snapshot("2024-01-15")
        self._backdate_snapshot(temp_dirs, "2024-01-15", date(2024, 1, 15))

        version_manager.create_snapshot("2024-01-31")
        self._backdate_snapshot(temp_dirs, "2024-01-31", date(2024, 1, 31))

        # Query as of Jan 20 - should get Jan 15
        path, snapshot = version_manager.query_as_of("crsp_daily", date(2024, 1, 20))

        assert snapshot.version_tag == "2024-01-15"

    def test_query_as_of_exact_date_match(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test query_as_of with exact date match."""
        # Setup
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)
        version_manager.create_snapshot("2024-01-15")
        self._backdate_snapshot(temp_dirs, "2024-01-15", date(2024, 1, 15))

        # Query exact date
        path, snapshot = version_manager.query_as_of("crsp_daily", date(2024, 1, 15))

        assert snapshot.version_tag == "2024-01-15"

    def test_query_as_of_no_snapshot_before_date(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test query_as_of raises when no snapshot exists before date."""
        # Setup
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)
        version_manager.create_snapshot("2024-06-01")
        self._backdate_snapshot(temp_dirs, "2024-06-01", date(2024, 6, 1))

        # Query before any snapshot
        with pytest.raises(SnapshotNotFoundError, match="No snapshot exists"):
            version_manager.query_as_of("crsp_daily", date(2024, 1, 1))

    def test_query_as_of_dataset_not_in_snapshot(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test query_as_of raises when dataset not in matched snapshot."""
        # Setup - create snapshot with only crsp
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)
        version_manager.create_snapshot("2024-01-15", datasets=["crsp_daily"])
        self._backdate_snapshot(temp_dirs, "2024-01-15", date(2024, 1, 15))

        # Query for compustat
        with pytest.raises(DatasetNotInSnapshotError):
            version_manager.query_as_of("compustat", date(2024, 1, 20))

    def test_query_as_of_ignores_non_date_tags(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test query_as_of ignores non-date-based version tags."""
        # Setup
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)

        # Create both date and non-date snapshots
        version_manager.create_snapshot("2024-01-15")
        self._backdate_snapshot(temp_dirs, "2024-01-15", date(2024, 1, 15))

        version_manager.create_snapshot("v1.0.0")  # Non-date tag
        version_manager.create_snapshot("release-candidate")  # Non-date tag

        # Query should only consider date-based
        path, snapshot = version_manager.query_as_of("crsp_daily", date(2024, 1, 20))

        assert snapshot.version_tag == "2024-01-15"

    def test_get_data_at_version_returns_correct_path(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test get_data_at_version returns correct file path."""
        # Setup
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)
        version_manager.create_snapshot("v1.0.0")

        # Get path
        path = version_manager.get_data_at_version("crsp_daily", "v1.0.0")

        # Verify path exists
        assert path.exists()
        assert path.is_dir()


# =============================================================================
# Backtest Linkage Tests (4 tests)
# =============================================================================


class TestBacktestLinkage(TestVersioningFixtures):
    """Tests for backtest-to-snapshot linkage."""

    def test_link_backtest_creates_durable_mapping(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test link_backtest creates durable linkage."""
        # Setup
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)
        version_manager.create_snapshot("v1.0.0")

        # Link
        linkage = version_manager.link_backtest("backtest-001", "v1.0.0")

        # Verify
        assert linkage.backtest_id == "backtest-001"
        assert linkage.snapshot_version == "v1.0.0"
        assert "crsp_daily" in linkage.dataset_versions

        # Verify persisted
        linkage_path = temp_dirs["backtests"] / "backtest-001.json"
        assert linkage_path.exists()

    def test_get_snapshot_for_backtest(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test retrieving snapshot for a backtest."""
        # Setup
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)
        version_manager.create_snapshot("v1.0.0")
        version_manager.link_backtest("backtest-001", "v1.0.0")

        # Retrieve
        snapshot = version_manager.get_snapshot_for_backtest("backtest-001")

        assert snapshot is not None
        assert snapshot.version_tag == "v1.0.0"

    def test_get_backtests_for_snapshot(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test getting all backtests linked to a snapshot."""
        # Setup
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)
        version_manager.create_snapshot("v1.0.0")
        version_manager.link_backtest("backtest-001", "v1.0.0")
        version_manager.link_backtest("backtest-002", "v1.0.0")
        version_manager.link_backtest("backtest-003", "v1.0.0")

        # Get backtests
        backtests = version_manager.get_backtests_for_snapshot("v1.0.0")

        assert len(backtests) == 3
        assert "backtest-001" in backtests
        assert "backtest-002" in backtests
        assert "backtest-003" in backtests

    def test_link_backtest_updates_snapshot_references(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test link_backtest updates snapshot's referenced_by list."""
        # Setup
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)
        version_manager.create_snapshot("v1.0.0")

        # Link
        version_manager.link_backtest("backtest-001", "v1.0.0")

        # Verify snapshot updated
        snapshot = version_manager.get_snapshot("v1.0.0")
        assert snapshot is not None
        assert "backtest-001" in snapshot.referenced_by


# =============================================================================
# Storage & CAS Tests (6 tests)
# =============================================================================


class TestStorageCAS(TestVersioningFixtures):
    """Tests for storage modes and CAS."""

    def test_copy_storage_preserves_immutability(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test copy storage mode is used for immutability (not hardlinks).

        Hardlinks are NOT used because they share the same inode as the source.
        If the source file is modified later, the snapshot would also change,
        breaking the immutability guarantee required for reproducible backtests.
        """
        # Setup
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs, "copy test")

        # Create snapshot with CAS disabled - should use copy (NOT hardlink)
        snapshot = version_manager.create_snapshot("copy-v1", use_cas=False)

        # Verify storage mode is copy (never hardlink for immutability)
        ds_snapshot = snapshot.datasets["crsp_daily"]
        for file_info in ds_snapshot.files:
            assert (
                file_info.storage_mode == "copy"
            ), f"Expected 'copy' for immutability, got '{file_info.storage_mode}'"

    def test_cas_storage_deduplicates(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test CAS storage deduplicates identical files."""
        # Setup - create two datasets with same content
        content = "identical content for dedup test"
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs, content)
        self._create_manifest_with_data(manifest_manager, "compustat", temp_dirs, content)

        # Force CAS mode by making hardlinks fail
        with patch("os.link", side_effect=OSError("Cross-device link")):
            _snapshot = version_manager.create_snapshot("cas-dedup-v1", use_cas=True)

        # Load CAS index
        cas_index = version_manager._load_cas_index()

        # Verify deduplication - should have only one CAS entry
        # (both files have same content -> same hash)
        cas_entries_with_refs = [e for e in cas_index.files.values() if e.ref_count >= 2]
        assert len(cas_entries_with_refs) >= 1

    def test_cas_ref_count_increments(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test CAS reference count increments on reuse."""
        # Setup
        content = "test content for ref count"
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs, content)

        # Force CAS mode by making hardlinks fail
        with patch("os.link", side_effect=OSError("Cross-device link")):
            # Create first snapshot
            version_manager.create_snapshot("cas-ref-v1", use_cas=True)

            # Create second snapshot (same data, should increment ref)
            version_manager.create_snapshot("cas-ref-v2", use_cas=True)

        # Load CAS index
        cas_index = version_manager._load_cas_index()

        # Find entry with ref_count >= 2
        high_ref_entries = [e for e in cas_index.files.values() if e.ref_count >= 2]
        assert len(high_ref_entries) >= 1

    def test_gc_cas_removes_unreferenced(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test gc_cas removes unreferenced CAS files."""
        # Setup - create snapshot with CAS forced
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)

        # Force CAS mode by making hardlinks fail
        with patch("os.link", side_effect=OSError("Cross-device link")):
            version_manager.create_snapshot("gc-test-v1", use_cas=True)

        # Get CAS file count before (exclude index file)
        cas_files_before = [
            f for f in temp_dirs["cas"].iterdir() if f.is_file() and f.name != "cas_index.json"
        ]
        assert len(cas_files_before) > 0, "Should have CAS files after snapshot"

        # Delete snapshot (which decrements ref counts)
        version_manager.delete_snapshot("gc-test-v1")

        # Run GC
        bytes_freed = version_manager.gc_cas()

        # Verify files removed (exclude index file)
        cas_files_after = [
            f for f in temp_dirs["cas"].iterdir() if f.is_file() and f.name != "cas_index.json"
        ]
        assert len(cas_files_after) < len(cas_files_before)
        assert bytes_freed > 0

    def test_storage_info_per_file(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test FileStorageInfo is tracked per file."""
        # Setup
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)
        snapshot = version_manager.create_snapshot("storage-info-v1")

        # Verify per-file storage info
        ds_snapshot = snapshot.datasets["crsp_daily"]
        for file_info in ds_snapshot.files:
            assert file_info.path is not None
            assert file_info.original_path is not None
            assert file_info.storage_mode in ("hardlink", "copy", "cas")
            assert file_info.size_bytes > 0
            assert file_info.checksum is not None

    def test_cas_storage_is_default_with_use_cas(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test CAS is used when use_cas=True (preferred for deduplication)."""
        # Setup
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)

        # Create snapshot with CAS enabled
        snapshot = version_manager.create_snapshot("cas-enabled-v1", use_cas=True)

        # Verify CAS was used (preferred for deduplication while maintaining immutability)
        ds_snapshot = snapshot.datasets["crsp_daily"]
        for file_info in ds_snapshot.files:
            assert file_info.storage_mode == "cas"


# =============================================================================
# Checksums & Integrity Tests (3 tests)
# =============================================================================


class TestChecksumsIntegrity(TestVersioningFixtures):
    """Tests for checksum verification and integrity."""

    def test_verify_snapshot_integrity_passes(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test verify_snapshot_integrity passes for valid snapshot."""
        # Setup
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)
        version_manager.create_snapshot("integrity-v1")

        # Verify
        errors = version_manager.verify_snapshot_integrity("integrity-v1")

        assert len(errors) == 0

    def test_verify_snapshot_integrity_detects_tampering(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test verify_snapshot_integrity detects file tampering."""
        # Setup - use copy mode to get actual files in snapshot dir
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)

        # Mock os.link to fail to force copy mode
        with patch("os.link", side_effect=OSError("Cross-device link")):
            version_manager.create_snapshot("tamper-v1", use_cas=False)

        # Tamper with a file
        snapshot_files_dir = temp_dirs["snapshots"] / "tamper-v1" / "files"
        for f in snapshot_files_dir.iterdir():
            if f.is_file():
                f.write_text("tampered content!")
                break

        # Verify
        errors = version_manager.verify_snapshot_integrity("tamper-v1")

        assert len(errors) > 0
        assert any("checksum" in e.lower() or "mismatch" in e.lower() for e in errors)

    def test_aggregate_checksum_hash_chain(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test aggregate checksum forms hash chain with previous snapshot."""
        # Setup
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)

        # Create first snapshot
        snapshot1 = version_manager.create_snapshot("chain-v1")
        assert snapshot1.prev_snapshot_checksum is None

        # Create second snapshot
        snapshot2 = version_manager.create_snapshot("chain-v2")
        assert snapshot2.prev_snapshot_checksum == snapshot1.aggregate_checksum

    def test_snapshot_immutability_after_source_modification(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test snapshot data is immutable even if source files are modified.

        This regression test ensures that modifying source files after snapshot
        creation does NOT affect the snapshot data. This is critical for
        reproducible backtests and auditability.
        """
        # Setup - create manifest with data
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)

        # Get source file (stored in data dir)
        source_file = temp_dirs["data"] / "crsp_daily.parquet"
        assert source_file.exists(), f"Source file not found: {source_file}"

        # Create snapshot (with copy mode, no CAS for direct comparison)
        snapshot = version_manager.create_snapshot("immut-v1", use_cas=False)

        # Record original snapshot checksum
        original_aggregate = snapshot.aggregate_checksum
        ds_snapshot = snapshot.datasets["crsp_daily"]
        original_file_checksums = {f.path: f.checksum for f in ds_snapshot.files}

        # Modify the source file (simulate data sync update)
        source_file.write_text("MODIFIED SOURCE DATA - should not affect snapshot!")

        # Verify snapshot integrity - should still pass
        errors = version_manager.verify_snapshot_integrity("immut-v1")
        assert len(errors) == 0, f"Snapshot corrupted after source modification: {errors}"

        # Re-read snapshot and verify checksums unchanged
        reloaded = version_manager.get_snapshot("immut-v1")
        assert reloaded is not None
        assert reloaded.aggregate_checksum == original_aggregate

        for file_info in reloaded.datasets["crsp_daily"].files:
            assert (
                file_info.checksum == original_file_checksums[file_info.path]
            ), f"Snapshot file {file_info.path} checksum changed after source modification"


# =============================================================================
# Error Handling & Recovery Tests (4 tests)
# =============================================================================


class TestErrorHandlingRecovery(TestVersioningFixtures):
    """Tests for error handling and recovery."""

    def test_create_snapshot_cleanup_on_failure(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test failed snapshot creation cleans up staging directory."""
        # Setup
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)

        # Force failure during snapshot creation
        with patch.object(
            version_manager,
            "_compute_aggregate_checksum",
            side_effect=RuntimeError("Simulated failure"),
        ):
            with pytest.raises(RuntimeError, match="Simulated failure"):
                version_manager.create_snapshot("cleanup-v1")

        # Verify no staging directories left
        staging_dirs = list(temp_dirs["snapshots"].glob(".staging_*"))
        assert len(staging_dirs) == 0

        # Verify no partial snapshot
        assert not (temp_dirs["snapshots"] / "cleanup-v1").exists()

    def test_missing_manifest_raises_error(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test snapshot creation fails if manifest is missing."""
        # Don't create any manifests

        with pytest.raises(ValueError, match="No datasets to snapshot"):
            version_manager.create_snapshot("no-data-v1")

    def test_snapshot_not_found_error(
        self,
        version_manager: DatasetVersionManager,
    ) -> None:
        """Test SnapshotNotFoundError raised for non-existent snapshot."""
        with pytest.raises(SnapshotNotFoundError):
            version_manager.get_data_at_version("crsp_daily", "nonexistent")

    def test_dataset_not_in_snapshot_error(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test DatasetNotInSnapshotError raised correctly."""
        # Setup - snapshot without compustat
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)
        version_manager.create_snapshot("partial-v1", datasets=["crsp_daily"])

        with pytest.raises(DatasetNotInSnapshotError) as exc_info:
            version_manager.get_data_at_version("compustat", "partial-v1")

        assert exc_info.value.version_tag == "partial-v1"
        assert exc_info.value.dataset == "compustat"


# =============================================================================
# Snapshot Deletion Tests (5 tests)
# =============================================================================


class TestSnapshotDeletion(TestVersioningFixtures):
    """Tests for snapshot deletion."""

    def test_delete_unreferenced_snapshot(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test deleting unreferenced snapshot succeeds."""
        # Setup
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)
        version_manager.create_snapshot("delete-v1")

        # Delete
        result = version_manager.delete_snapshot("delete-v1")

        assert result is True
        assert version_manager.get_snapshot("delete-v1") is None

    def test_delete_referenced_snapshot_blocked(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test deleting referenced snapshot is blocked."""
        # Setup
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)
        version_manager.create_snapshot("protected-v1")
        version_manager.link_backtest("backtest-001", "protected-v1")

        # Try delete without force
        with pytest.raises(SnapshotReferencedError) as exc_info:
            version_manager.delete_snapshot("protected-v1")

        assert "backtest-001" in exc_info.value.referenced_by

    def test_delete_referenced_snapshot_with_force(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test force delete of referenced snapshot succeeds."""
        # Setup
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)
        version_manager.create_snapshot("force-delete-v1")
        version_manager.link_backtest("backtest-001", "force-delete-v1")

        # Force delete
        result = version_manager.delete_snapshot("force-delete-v1", force=True)

        assert result is True
        assert version_manager.get_snapshot("force-delete-v1") is None

    def test_delete_orphans_backtest_linkages(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test delete with force marks backtest linkages as orphaned."""
        # Setup
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)
        version_manager.create_snapshot("orphan-v1")
        version_manager.link_backtest("backtest-orphan", "orphan-v1")

        # Force delete
        version_manager.delete_snapshot("orphan-v1", force=True)

        # Verify linkage marked orphaned
        linkage_path = temp_dirs["backtests"] / "backtest-orphan.json"
        with open(linkage_path) as f:
            linkage_data = json.load(f)

        assert linkage_data["orphaned_at"] is not None

    def test_delete_releases_cas_refs(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test delete properly releases CAS references."""
        # Setup
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)

        # Force CAS mode by making hardlinks fail
        with patch("os.link", side_effect=OSError("Cross-device link")):
            version_manager.create_snapshot("cas-release-v1", use_cas=True)

        # Get ref count before delete
        cas_index_before = version_manager._load_cas_index()
        total_refs_before = sum(e.ref_count for e in cas_index_before.files.values())
        assert total_refs_before > 0, "Should have CAS refs after snapshot"

        # Delete
        version_manager.delete_snapshot("cas-release-v1")

        # Get ref count after delete
        cas_index_after = version_manager._load_cas_index()
        total_refs_after = sum(e.ref_count for e in cas_index_after.files.values())

        # Refs should have decreased
        assert total_refs_after < total_refs_before


# =============================================================================
# Retention Policy Tests (2 tests)
# =============================================================================


class TestRetentionPolicy(TestVersioningFixtures):
    """Tests for retention policy."""

    def test_retention_deletes_old_unreferenced(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test retention policy deletes old unreferenced snapshots."""
        # Setup
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)

        # Create snapshot
        _snapshot = version_manager.create_snapshot("old-snapshot")

        # Manually backdate the snapshot
        snapshot_path = temp_dirs["snapshots"] / "old-snapshot" / "manifest.json"
        with open(snapshot_path) as f:
            data = json.load(f)

        old_date = datetime.now(UTC) - timedelta(days=100)
        data["created_at"] = old_date.isoformat()
        with open(snapshot_path, "w") as f:
            json.dump(data, f)

        # Run retention
        deleted = version_manager.enforce_retention_policy()

        assert "old-snapshot" in deleted

    def test_retention_preserves_referenced(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test retention policy preserves referenced snapshots even if old."""
        # Setup
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)

        # Create and link snapshot
        version_manager.create_snapshot("protected-old")
        version_manager.link_backtest("important-backtest", "protected-old")

        # Manually backdate
        snapshot_path = temp_dirs["snapshots"] / "protected-old" / "manifest.json"
        with open(snapshot_path) as f:
            data = json.load(f)

        old_date = datetime.now(UTC) - timedelta(days=100)
        data["created_at"] = old_date.isoformat()
        with open(snapshot_path, "w") as f:
            json.dump(data, f)

        # Run retention
        deleted = version_manager.enforce_retention_policy()

        # Should NOT be deleted
        assert "protected-old" not in deleted
        assert version_manager.get_snapshot("protected-old") is not None


# =============================================================================
# Edge Cases Tests (2 tests)
# =============================================================================


class TestEdgeCases(TestVersioningFixtures):
    """Tests for edge cases."""

    def test_empty_snapshot_rejected(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test creating snapshot with no datasets is rejected."""
        # Setup - create manifest but try to snapshot non-existent datasets
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)

        with pytest.raises(DataNotFoundError, match="nonexistent"):
            version_manager.create_snapshot("empty-v1", datasets=["nonexistent"])

    def test_concurrent_snapshot_creation_safe(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test concurrent snapshot creation doesn't corrupt state."""
        # Setup
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)

        # Create multiple snapshots (simulating concurrent-ish creation)
        version_manager.create_snapshot("concurrent-v1")
        version_manager.create_snapshot("concurrent-v2")
        version_manager.create_snapshot("concurrent-v3")

        # Verify all exist and are valid
        for tag in ["concurrent-v1", "concurrent-v2", "concurrent-v3"]:
            snapshot = version_manager.get_snapshot(tag)
            assert snapshot is not None
            assert snapshot.version_tag == tag
            errors = version_manager.verify_snapshot_integrity(tag)
            assert len(errors) == 0


# =============================================================================
# Pydantic Model Tests
# =============================================================================


class TestPydanticModels:
    """Tests for Pydantic model validation."""

    def test_snapshot_manifest_utc_validation(self) -> None:
        """Test SnapshotManifest requires UTC timestamp."""
        from zoneinfo import ZoneInfo

        # Valid UTC
        now_utc = datetime.now(UTC)
        manifest = SnapshotManifest(
            version_tag="test",
            created_at=now_utc,
            datasets={},
            total_size_bytes=0,
            aggregate_checksum="abc123",
        )
        assert manifest.created_at.tzinfo is not None

        # Invalid: non-UTC timezone
        from pydantic import ValidationError

        est = ZoneInfo("America/New_York")
        with pytest.raises(ValidationError, match="UTC"):
            SnapshotManifest(
                version_tag="test",
                created_at=datetime.now(est),
                datasets={},
                total_size_bytes=0,
                aggregate_checksum="abc123",
            )

    def test_file_storage_info_immutable(self) -> None:
        """Test FileStorageInfo is immutable (frozen)."""
        from pydantic import ValidationError

        info = FileStorageInfo(
            path="test.parquet",
            original_path="/data/test.parquet",
            storage_mode="copy",
            target="/snapshot/test.parquet",
            size_bytes=1000,
            checksum="abc123",
        )

        # Should raise on modification
        with pytest.raises(ValidationError):
            info.path = "modified.parquet"

    def test_backtest_linkage_checksum(self) -> None:
        """Test BacktestLinkage has valid checksum."""
        linkage = BacktestLinkage(
            backtest_id="bt-001",
            created_at=datetime.now(UTC),
            snapshot_version="v1.0.0",
            dataset_versions={"crsp": 1},
            checksum="def456" * 10,
        )

        assert linkage.checksum is not None
        assert len(linkage.checksum) > 0
