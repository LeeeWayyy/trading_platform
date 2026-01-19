"""Tests for libs.data_quality.versioning module.

Test coverage: 73 test cases organized into 17 categories:
- Core Functionality (5 tests)
- Consistency & Locking (3 tests)
- Time-Travel (6 tests)
- Backtest Linkage (4 tests)
- Storage & CAS (6 tests)
- Checksums & Integrity (3 tests)
- Error Handling & Recovery (4 tests)
- Snapshot Deletion (5 tests)
- Retention Policy (2 tests)
- Edge Cases (2 tests)
- Pydantic Models (3 tests)
- Path Security & Validation (8 tests)
- Lock Management (6 tests)
- CAS Operations (4 tests)
- File Operations (5 tests)
- Atomic Write (3 tests)
- Base64 Encoding (3 tests)
- Diff Models (2 tests)
- Additional Edge Cases (4 tests)

Coverage areas:
- Version tag validation and path traversal prevention
- Lock acquisition, stale lock detection, and process liveness checks
- CAS operations including deduplication, reference counting, and GC
- File operations with fsync and checksum verification
- Atomic JSON writes with error handling
- Base64 encoding/decoding for binary data in diffs
- Diff models for snapshot changes tracking
- Security validation for file paths and identifiers
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


# =============================================================================
# Path Security & Validation Tests (8 tests)
# =============================================================================


class TestPathSecurityValidation(TestVersioningFixtures):
    """Tests for path traversal prevention and file path validation."""

    def test_validate_identifier_rejects_empty(
        self,
        version_manager: DatasetVersionManager,
    ) -> None:
        """Test _validate_identifier rejects empty strings."""
        # Empty strings fail pattern validation (must start with alphanumeric)
        with pytest.raises(ValueError, match="Invalid test:"):
            version_manager._validate_identifier("", version_manager.snapshots_dir, "test")

    def test_validate_identifier_rejects_path_traversal(
        self,
        version_manager: DatasetVersionManager,
    ) -> None:
        """Test _validate_identifier detects path traversal attempts.

        Note: Path traversal attempts like '../' fail the SAFE_TAG_PATTERN check first,
        which rejects any identifier not matching [A-Za-z0-9][A-Za-z0-9._-]*.
        This is the intended behavior - pattern validation is the first line of defense.
        """
        traversal_attempts = [
            "../etc",
            "../../passwd",
            "foo/../../../etc",
            "foo/../../bar",
        ]
        for attempt in traversal_attempts:
            # Pattern validation rejects these before path traversal check
            with pytest.raises(ValueError, match="Invalid version_tag:"):
                version_manager._validate_identifier(
                    attempt, version_manager.snapshots_dir, "version_tag"
                )

    def test_validate_identifier_rejects_special_chars(
        self,
        version_manager: DatasetVersionManager,
    ) -> None:
        """Test _validate_identifier rejects invalid characters."""
        invalid_identifiers = [
            "foo/bar",
            "foo\\bar",
            "foo bar",  # Space
            "foo\x00bar",  # Null byte
            "foo:bar",  # Colon (path separator on Windows)
        ]
        for invalid in invalid_identifiers:
            with pytest.raises(ValueError, match="Invalid"):
                version_manager._validate_identifier(
                    invalid, version_manager.snapshots_dir, "identifier"
                )

    def test_validate_identifier_accepts_valid(
        self,
        version_manager: DatasetVersionManager,
    ) -> None:
        """Test _validate_identifier accepts valid identifiers."""
        valid_identifiers = [
            "v1.0.0",
            "2024-01-15",
            "snapshot_123",
            "my-snapshot",
            "a1b2c3",
            "BackTest-2024.Q1",
        ]
        for valid in valid_identifiers:
            # Should not raise
            version_manager._validate_identifier(valid, version_manager.snapshots_dir, "test")

    def test_validate_file_path_rejects_outside_data_root(
        self,
        version_manager: DatasetVersionManager,
        tmp_path: Path,
    ) -> None:
        """Test _validate_file_path rejects files outside data_root."""
        # Create file outside data root
        outside_file = tmp_path.parent / "outside.txt"
        outside_file.write_text("outside data root")

        with pytest.raises(ValueError, match="outside data root"):
            version_manager._validate_file_path(outside_file)

    def test_validate_file_path_rejects_symlinks(
        self,
        version_manager: DatasetVersionManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test _validate_file_path rejects symlinks."""
        # Create a real file
        real_file = temp_dirs["data"] / "real.txt"
        real_file.write_text("real file")

        # Create symlink pointing to it
        symlink = temp_dirs["data"] / "link.txt"
        symlink.symlink_to(real_file)

        with pytest.raises(ValueError, match="Symlinks not allowed"):
            version_manager._validate_file_path(symlink)

    def test_validate_file_path_accepts_valid(
        self,
        version_manager: DatasetVersionManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test _validate_file_path accepts valid paths within data_root."""
        valid_file = temp_dirs["data"] / "valid.txt"
        valid_file.write_text("valid file")

        # Should not raise
        version_manager._validate_file_path(valid_file)

    def test_get_snapshot_validates_version_tag(
        self,
        version_manager: DatasetVersionManager,
    ) -> None:
        """Test get_snapshot validates version_tag for path traversal.

        Note: Path traversal attempts fail pattern validation first (SAFE_TAG_PATTERN).
        """
        with pytest.raises(ValueError, match="Invalid version_tag:"):
            version_manager.get_snapshot("../../../etc/passwd")


# =============================================================================
# Lock Management Tests (6 tests)
# =============================================================================


class TestLockManagement(TestVersioningFixtures):
    """Tests for lock acquisition and stale lock detection."""

    def test_acquire_snapshot_lock_basic(
        self,
        version_manager: DatasetVersionManager,
    ) -> None:
        """Test basic snapshot lock acquisition."""
        with version_manager._acquire_snapshot_lock():
            # Lock acquired successfully
            lock_path = version_manager.locks_dir / "snapshots.lock"
            assert lock_path.exists()

        # Lock released after context exit
        assert not lock_path.exists()

    def test_acquire_snapshot_lock_prevents_concurrent(
        self,
        version_manager: DatasetVersionManager,
    ) -> None:
        """Test snapshot lock prevents concurrent operations."""
        from libs.data.data_quality.exceptions import LockNotHeldError

        # Manually create lock file
        lock_path = version_manager.locks_dir / "snapshots.lock"
        lock_data = {
            "pid": os.getpid(),  # Same process - should block
            "hostname": version_manager._get_hostname(),
            "acquired_at": datetime.now(UTC).isoformat(),
        }
        with open(lock_path, "w") as f:
            json.dump(lock_data, f)

        try:
            # Use very short timeout for test speed
            original_timeout = version_manager.SNAPSHOT_LOCK_TIMEOUT_SECONDS
            version_manager.SNAPSHOT_LOCK_TIMEOUT_SECONDS = 0.2
            try:
                # Should fail quickly (within timeout)
                with pytest.raises(LockNotHeldError, match="Failed to acquire"):
                    with version_manager._acquire_snapshot_lock():
                        pass
            finally:
                version_manager.SNAPSHOT_LOCK_TIMEOUT_SECONDS = original_timeout
        finally:
            # Clean up
            lock_path.unlink(missing_ok=True)

    def test_is_lock_stale_detects_dead_process(
        self,
        version_manager: DatasetVersionManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test _is_lock_stale detects locks from dead processes."""
        # Create lock from a PID that definitely doesn't exist
        dead_pid = 99999999  # Very unlikely to exist
        lock_path = temp_dirs["locks"] / "test.lock"
        lock_data = {
            "pid": dead_pid,
            "hostname": version_manager._get_hostname(),  # Same host
            "acquired_at": datetime.now(UTC).isoformat(),
        }
        with open(lock_path, "w") as f:
            json.dump(lock_data, f)

        # Should detect as stale
        assert version_manager._is_lock_stale(lock_path) is True

    def test_is_lock_stale_preserves_live_process(
        self,
        version_manager: DatasetVersionManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test _is_lock_stale does not evict locks from live processes."""
        # Create lock from current process
        lock_path = temp_dirs["locks"] / "test.lock"
        lock_data = {
            "pid": os.getpid(),  # Current process - alive
            "hostname": version_manager._get_hostname(),
            "acquired_at": datetime.now(UTC).isoformat(),
        }
        with open(lock_path, "w") as f:
            json.dump(lock_data, f)

        # Should NOT be stale (process is alive)
        assert version_manager._is_lock_stale(lock_path) is False

    def test_is_lock_stale_uses_timeout_for_different_host(
        self,
        version_manager: DatasetVersionManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test _is_lock_stale uses timeout for locks from different hosts."""
        # Create old lock from different host
        lock_path = temp_dirs["locks"] / "test.lock"
        old_time = datetime.now(UTC) - timedelta(hours=2)  # > STALE_LOCK_TIMEOUT
        lock_data = {
            "pid": 12345,
            "hostname": "different-host",
            "acquired_at": old_time.isoformat(),
        }
        with open(lock_path, "w") as f:
            json.dump(lock_data, f)

        # Should be stale due to timeout
        assert version_manager._is_lock_stale(lock_path) is True

    def test_is_lock_stale_handles_corrupted_lock(
        self,
        version_manager: DatasetVersionManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test _is_lock_stale treats corrupted lock files as stale."""
        # Create corrupted lock file
        lock_path = temp_dirs["locks"] / "test.lock"
        lock_path.write_text("not valid json {{{")

        # Should treat as stale
        assert version_manager._is_lock_stale(lock_path) is True


# =============================================================================
# CAS Operations Tests (4 tests)
# =============================================================================


class TestCASOperations(TestVersioningFixtures):
    """Tests for Content-Addressable Storage operations."""

    def test_get_cas_path_ignores_extension(
        self,
        version_manager: DatasetVersionManager,
    ) -> None:
        """Test _get_cas_path uses checksum-only naming."""
        checksum = "abc123def456"

        # Different extensions should give same CAS path
        path1 = version_manager._get_cas_path(checksum, Path("file.parquet"))
        path2 = version_manager._get_cas_path(checksum, Path("file.csv"))
        path3 = version_manager._get_cas_path(checksum, None)

        assert path1 == path2 == path3
        assert path1.name == checksum  # No extension

    def test_safe_copy_to_cas_verifies_checksum(
        self,
        version_manager: DatasetVersionManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test _safe_copy_to_cas verifies checksum after copy."""
        # Create source file
        src = temp_dirs["data"] / "source.txt"
        src.write_text("test content")

        # Compute correct checksum
        checksum = version_manager.validator.compute_checksum(src)

        # Copy to CAS
        dest = temp_dirs["cas"] / checksum
        version_manager._safe_copy_to_cas(src, dest, checksum)

        # Verify file copied
        assert dest.exists()
        assert dest.read_text() == "test content"

    def test_safe_copy_to_cas_rejects_wrong_checksum(
        self,
        version_manager: DatasetVersionManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test _safe_copy_to_cas raises on checksum mismatch."""
        # Create source file
        src = temp_dirs["data"] / "source.txt"
        src.write_text("test content")

        # Use WRONG checksum
        wrong_checksum = "wrong123" * 8

        dest = temp_dirs["cas"] / wrong_checksum
        with pytest.raises(ValueError, match="checksum mismatch"):
            version_manager._safe_copy_to_cas(src, dest, wrong_checksum)

        # Verify temp file cleaned up
        temp_files = list(temp_dirs["cas"].glob(".tmp_*"))
        assert len(temp_files) == 0

    def test_gc_cas_removes_orphaned_files(
        self,
        version_manager: DatasetVersionManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test gc_cas removes orphaned CAS files not in index."""
        # Create orphaned CAS file (not in index)
        orphan_hash = "orphan123" * 8
        orphan_file = temp_dirs["cas"] / orphan_hash
        orphan_file.write_text("orphaned content")

        # Run GC
        bytes_freed = version_manager.gc_cas()

        # Orphaned file should be removed
        assert not orphan_file.exists()
        assert bytes_freed > 0


# =============================================================================
# File Operations Tests (5 tests)
# =============================================================================


class TestFileOperations(TestVersioningFixtures):
    """Tests for file copy and fsync operations."""

    def test_copy_with_fsync_preserves_content(
        self,
        version_manager: DatasetVersionManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test _copy_with_fsync preserves file content."""
        src = temp_dirs["data"] / "source.txt"
        src.write_text("test content")

        dest = temp_dirs["data"] / "dest.txt"
        version_manager._copy_with_fsync(src, dest)

        assert dest.read_text() == "test content"

    def test_copy_with_fsync_verifies_checksum(
        self,
        version_manager: DatasetVersionManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test _copy_with_fsync verifies checksum when provided."""
        src = temp_dirs["data"] / "source.txt"
        src.write_text("test content")

        checksum = version_manager.validator.compute_checksum(src)

        dest = temp_dirs["data"] / "dest.txt"
        version_manager._copy_with_fsync(src, dest, checksum)

        assert dest.exists()

    def test_copy_with_fsync_rejects_wrong_checksum(
        self,
        version_manager: DatasetVersionManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test _copy_with_fsync raises on checksum mismatch."""
        src = temp_dirs["data"] / "source.txt"
        src.write_text("test content")

        wrong_checksum = "wrong123" * 8

        dest = temp_dirs["data"] / "dest.txt"
        with pytest.raises(ValueError, match="checksum mismatch"):
            version_manager._copy_with_fsync(src, dest, wrong_checksum)

        # Destination should be cleaned up
        assert not dest.exists()

    def test_fsync_directory_handles_unsupported(
        self,
        version_manager: DatasetVersionManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test _fsync_directory handles unsupported filesystems gracefully."""
        # Mock os.open to fail for directory fsync
        with patch("os.open", side_effect=OSError("Not supported")):
            # Should not raise (just log warning)
            version_manager._fsync_directory(temp_dirs["data"])

    def test_fsync_file_syncs_data(
        self,
        version_manager: DatasetVersionManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test _fsync_file syncs file data."""
        test_file = temp_dirs["data"] / "test.txt"
        test_file.write_text("test")

        # Should not raise
        version_manager._fsync_file(test_file)


# =============================================================================
# Atomic Write Tests (3 tests)
# =============================================================================


class TestAtomicWrite(TestVersioningFixtures):
    """Tests for atomic JSON write operations."""

    def test_atomic_write_json_creates_file(
        self,
        version_manager: DatasetVersionManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test _atomic_write_json creates file with correct content."""
        path = temp_dirs["data"] / "test.json"
        data = {"key": "value", "number": 42}

        version_manager._atomic_write_json(path, data)

        assert path.exists()
        with open(path) as f:
            loaded = json.load(f)
        assert loaded == data

    def test_atomic_write_json_handles_oserror(
        self,
        version_manager: DatasetVersionManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test _atomic_write_json cleans up on OSError."""
        path = temp_dirs["data"] / "test.json"

        # Mock os.fsync to fail
        with patch("os.fsync", side_effect=OSError("Disk full")):
            with pytest.raises(OSError, match="Disk full"):
                version_manager._atomic_write_json(path, {"key": "value"})

        # Temp file should be cleaned up
        temp_files = list(temp_dirs["data"].glob("test_*.tmp"))
        assert len(temp_files) == 0

    def test_atomic_write_json_handles_non_serializable(
        self,
        version_manager: DatasetVersionManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test _atomic_write_json handles non-serializable data via default=str.

        The implementation uses json.dump(..., default=str), so non-serializable
        objects are converted to their string representation rather than raising
        TypeError. This is intentional to allow graceful handling of complex objects.
        """
        path = temp_dirs["data"] / "test.json"

        # Try to serialize non-serializable object
        class NonSerializable:
            pass

        # Should succeed - default=str converts the object to its string representation
        version_manager._atomic_write_json(path, {"obj": NonSerializable()})

        # Verify file was created with string representation
        assert path.exists()
        with open(path) as f:
            data = json.load(f)
        # The class instance gets converted to a string like "<...NonSerializable object at 0x...>"
        assert "obj" in data
        assert "NonSerializable" in data["obj"]


# =============================================================================
# Base64 Encoding Tests (3 tests)
# =============================================================================


class TestBase64Encoding:
    """Tests for base64 encoding/decoding utilities."""

    def test_decode_base64_bytes_from_string(self) -> None:
        """Test _decode_base64_bytes decodes base64 strings."""
        from libs.data.data_quality.versioning import _decode_base64_bytes

        # Encode test data
        test_data = b"test binary data"
        import base64

        encoded = base64.b64encode(test_data).decode("ascii")

        # Decode
        result = _decode_base64_bytes(encoded)
        assert result == test_data

    def test_decode_base64_bytes_passes_through_bytes(self) -> None:
        """Test _decode_base64_bytes passes through bytes unchanged."""
        from libs.data.data_quality.versioning import _decode_base64_bytes

        test_data = b"test bytes"
        result = _decode_base64_bytes(test_data)
        assert result == test_data

    def test_decode_base64_bytes_handles_none(self) -> None:
        """Test _decode_base64_bytes handles None."""
        from libs.data.data_quality.versioning import _decode_base64_bytes

        result = _decode_base64_bytes(None)
        assert result is None


# =============================================================================
# Diff Model Tests (2 tests)
# =============================================================================


class TestDiffModels:
    """Tests for diff data models."""

    def test_diff_file_entry_with_inline_data(self) -> None:
        """Test DiffFileEntry with inline binary data."""
        from libs.data.data_quality.versioning import DiffFileEntry

        entry = DiffFileEntry(
            path="test.txt",
            old_hash="abc123",
            new_hash="def456",
            storage="inline",
            inline_data=b"test content",
        )

        # Should serialize to JSON (via base64)
        data = entry.model_dump(mode="json")
        assert "inline_data" in data
        assert isinstance(data["inline_data"], str)  # Base64 encoded

        # Should deserialize back
        restored = DiffFileEntry.model_validate(data)
        assert restored.inline_data == b"test content"

    def test_snapshot_diff_tracks_changes(self) -> None:
        """Test SnapshotDiff tracks file changes."""
        from libs.data.data_quality.versioning import DiffFileEntry, SnapshotDiff

        diff = SnapshotDiff(
            from_version="v1",
            to_version="v2",
            created_at=datetime.now(UTC),
            added_files=[
                DiffFileEntry(
                    path="new.txt",
                    old_hash=None,
                    new_hash="abc123",
                    storage="cas",
                    cas_hash="abc123",
                )
            ],
            removed_files=["old.txt"],
            changed_files=[
                DiffFileEntry(
                    path="modified.txt",
                    old_hash="old123",
                    new_hash="new456",
                    storage="inline",
                    inline_data=b"new content",
                )
            ],
            checksum="diff123" * 8,
        )

        assert len(diff.added_files) == 1
        assert len(diff.removed_files) == 1
        assert len(diff.changed_files) == 1
        assert diff.orphaned_at is None


# =============================================================================
# Additional Edge Cases (4 tests)
# =============================================================================


class TestAdditionalEdgeCases(TestVersioningFixtures):
    """Tests for additional edge cases."""

    def test_create_snapshot_with_missing_file(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test snapshot creation fails if manifest references file that gets deleted.

        The manifest_manager.save_manifest validates file paths, so we must:
        1. Create the file first
        2. Save the manifest
        3. Delete the file
        4. Verify snapshot creation fails
        """
        # Create the file first (required for save_manifest validation)
        data_file = temp_dirs["data"] / "to_be_deleted.parquet"
        data_file.write_bytes(b"temporary parquet content")

        lock_path = temp_dirs["locks"] / "test_dataset.lock"
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

        manifest = SyncManifest(
            dataset="test_dataset",
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
        manifest_manager.save_manifest(manifest, token)

        # Now delete the file to simulate it going missing
        data_file.unlink()

        # Should fail with DataNotFoundError when file is missing during snapshot
        with pytest.raises(DataNotFoundError, match="not found"):
            version_manager.create_snapshot("missing-file-v1")

    def test_link_backtest_validates_backtest_id(
        self,
        version_manager: DatasetVersionManager,
        manifest_manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test link_backtest validates backtest_id for path traversal.

        Note: Path traversal attempts fail pattern validation first (SAFE_TAG_PATTERN).
        """
        self._create_manifest_with_data(manifest_manager, "crsp_daily", temp_dirs)
        version_manager.create_snapshot("v1.0.0")

        # Pattern validation rejects path traversal attempts
        with pytest.raises(ValueError, match="Invalid backtest_id:"):
            version_manager.link_backtest("../../../etc/passwd", "v1.0.0")

    def test_get_snapshot_for_backtest_validates_id(
        self,
        version_manager: DatasetVersionManager,
    ) -> None:
        """Test get_snapshot_for_backtest validates backtest_id.

        Note: Path traversal attempts fail pattern validation first (SAFE_TAG_PATTERN).
        """
        with pytest.raises(ValueError, match="Invalid backtest_id:"):
            version_manager.get_snapshot_for_backtest("../../evil")

    def test_is_process_alive_detects_dead_process(
        self,
        version_manager: DatasetVersionManager,
    ) -> None:
        """Test _is_process_alive detects dead processes."""
        # PID that definitely doesn't exist
        dead_pid = 99999999
        assert version_manager._is_process_alive(dead_pid) is False

        # Current process should be alive
        assert version_manager._is_process_alive(os.getpid()) is True
