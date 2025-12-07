"""Tests for libs.data_quality.manifest module."""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from libs.data_quality.exceptions import DiskSpaceError, LockNotHeldError
from libs.data_quality.manifest import ManifestManager, SyncManifest
from libs.data_quality.types import LockToken


class TestSyncManifest:
    """Tests for SyncManifest Pydantic model."""

    def _create_valid_manifest(self, **overrides) -> dict:
        """Create valid manifest data with optional overrides."""
        data = {
            "dataset": "crsp_daily",
            "sync_timestamp": datetime.now(UTC),
            "start_date": date(2024, 1, 1),
            "end_date": date(2024, 1, 31),
            "row_count": 10000,
            "checksum": "abc123" * 10,
            "schema_version": "v1.0.0",
            "wrds_query_hash": "def456" * 10,
            "file_paths": ["data/crsp_daily.parquet"],
            "validation_status": "passed",
        }
        data.update(overrides)
        return data

    def test_serialization_deserialization(self) -> None:
        """Test SyncManifest serializes and deserializes correctly."""
        data = self._create_valid_manifest()
        manifest = SyncManifest(**data)

        # Serialize to JSON
        json_str = manifest.model_dump_json()

        # Deserialize back
        restored = SyncManifest.model_validate_json(json_str)

        assert restored.dataset == manifest.dataset
        assert restored.row_count == manifest.row_count
        assert restored.validation_status == manifest.validation_status

    def test_utc_timestamp_with_offset_zero_passes(self) -> None:
        """Test UTC timestamp with offset == 0 passes validation."""
        data = self._create_valid_manifest(
            sync_timestamp=datetime.now(UTC)
        )
        manifest = SyncManifest(**data)

        assert manifest.sync_timestamp.tzinfo is not None
        assert manifest.sync_timestamp.utcoffset().total_seconds() == 0

    def test_non_utc_timezone_rejected(self) -> None:
        """Test non-UTC timezone (e.g., EST, PST) is rejected."""
        # Use a timezone with non-zero offset
        est = ZoneInfo("America/New_York")
        non_utc_timestamp = datetime.now(est)

        data = self._create_valid_manifest(sync_timestamp=non_utc_timestamp)

        with pytest.raises(ValidationError) as exc_info:
            SyncManifest(**data)

        assert "UTC" in str(exc_info.value)

    def test_naive_timestamp_rejected(self) -> None:
        """Test naive (timezone-unaware) timestamp is rejected."""
        naive_timestamp = datetime.now()  # No timezone

        data = self._create_valid_manifest(sync_timestamp=naive_timestamp)

        with pytest.raises(ValidationError) as exc_info:
            SyncManifest(**data)

        assert "timezone-aware" in str(exc_info.value)

    def test_start_date_lte_end_date_passes(self) -> None:
        """Test start_date <= end_date passes validation."""
        data = self._create_valid_manifest(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
        )
        manifest = SyncManifest(**data)

        assert manifest.start_date <= manifest.end_date

    def test_start_date_equals_end_date_passes(self) -> None:
        """Test start_date == end_date passes validation."""
        data = self._create_valid_manifest(
            start_date=date(2024, 1, 15),
            end_date=date(2024, 1, 15),
        )
        manifest = SyncManifest(**data)

        assert manifest.start_date == manifest.end_date

    def test_start_date_gt_end_date_rejected(self) -> None:
        """Test start_date > end_date is rejected."""
        data = self._create_valid_manifest(
            start_date=date(2024, 1, 31),
            end_date=date(2024, 1, 1),
        )

        with pytest.raises(ValidationError) as exc_info:
            SyncManifest(**data)

        assert "end_date must be >= start_date" in str(exc_info.value)

    def test_file_paths_non_empty_passes(self) -> None:
        """Test non-empty file_paths passes validation."""
        data = self._create_valid_manifest(
            file_paths=["file1.parquet", "file2.parquet"]
        )
        manifest = SyncManifest(**data)

        assert len(manifest.file_paths) == 2

    def test_file_paths_empty_rejected(self) -> None:
        """Test empty file_paths is rejected."""
        data = self._create_valid_manifest(file_paths=[])

        with pytest.raises(ValidationError) as exc_info:
            SyncManifest(**data)

        assert "file_paths must not be empty" in str(exc_info.value)


class TestManifestManager:
    """Tests for ManifestManager class."""

    @pytest.fixture()
    def temp_dirs(self, tmp_path: Path) -> dict[str, Path]:
        """Create temporary directories for testing."""
        storage = tmp_path / "manifests"
        locks = tmp_path / "locks"
        backups = tmp_path / "backups"
        quarantine = tmp_path / "quarantine"

        for d in [storage, locks, backups, quarantine]:
            d.mkdir(parents=True)

        return {
            "storage": storage,
            "locks": locks,
            "backups": backups,
            "quarantine": quarantine,
        }

    @pytest.fixture()
    def manager(self, temp_dirs: dict[str, Path], tmp_path: Path) -> ManifestManager:
        """Create ManifestManager with temp directories."""
        return ManifestManager(
            storage_path=temp_dirs["storage"],
            lock_dir=temp_dirs["locks"],
            backup_dir=temp_dirs["backups"],
            quarantine_dir=temp_dirs["quarantine"],
            data_root=tmp_path,  # Allow files within temp directory
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

        # Create lock file
        with open(lock_path, "w") as f:
            json.dump(token.to_dict(), f)

        return token

    def test_load_manifest_returns_none_if_not_found(
        self, manager: ManifestManager
    ) -> None:
        """Test load_manifest returns None if manifest doesn't exist."""
        result = manager.load_manifest("nonexistent")
        assert result is None

    def test_atomic_write_creates_file(
        self, manager: ManifestManager, temp_dirs: dict[str, Path]
    ) -> None:
        """Test atomic write creates file correctly."""
        path = temp_dirs["storage"] / "test.json"
        data = {"key": "value"}

        manager._atomic_write(path, data)

        assert path.exists()
        with open(path) as f:
            loaded = json.load(f)
        assert loaded["key"] == "value"

    def test_assert_lock_held_success(
        self, manager: ManifestManager, valid_lock_token: LockToken
    ) -> None:
        """Test assert_lock_held succeeds with valid lock."""
        # Should not raise
        manager.assert_lock_held(valid_lock_token)

    def test_assert_lock_held_fails_with_expired_lock(
        self, manager: ManifestManager, temp_dirs: dict[str, Path]
    ) -> None:
        """Test assert_lock_held fails when lock is expired."""
        now = datetime.now(UTC)
        lock_path = temp_dirs["locks"] / "expired.lock"

        expired_token = LockToken(
            pid=os.getpid(),
            hostname="test-host",
            writer_id="test-writer",
            acquired_at=now - timedelta(hours=5),
            expires_at=now - timedelta(hours=1),  # Expired
            lock_path=lock_path,
        )

        with open(lock_path, "w") as f:
            json.dump(expired_token.to_dict(), f)

        with pytest.raises(LockNotHeldError) as exc_info:
            manager.assert_lock_held(expired_token)

        assert "expired" in str(exc_info.value).lower()

    def test_assert_lock_held_fails_with_mismatched_token(
        self, manager: ManifestManager, temp_dirs: dict[str, Path]
    ) -> None:
        """Test assert_lock_held fails when token doesn't match lock file."""
        now = datetime.now(UTC)
        lock_path = temp_dirs["locks"] / "mismatch.lock"

        # Create lock file with one PID
        file_data = {
            "pid": 99999,  # Different PID
            "hostname": "test-host",
            "writer_id": "test-writer",
            "acquired_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=4)).isoformat(),
        }
        with open(lock_path, "w") as f:
            json.dump(file_data, f)

        # Token has different PID
        token = LockToken(
            pid=12345,  # Mismatched
            hostname="test-host",
            writer_id="test-writer",
            acquired_at=now,
            expires_at=now + timedelta(hours=4),
            lock_path=lock_path,
        )

        with pytest.raises(LockNotHeldError) as exc_info:
            manager.assert_lock_held(token)

        assert "mismatch" in str(exc_info.value).lower()

    def test_check_disk_space_80_percent_warning(
        self, manager: ManifestManager
    ) -> None:
        """Test check_disk_space returns warning at 80% usage."""
        with patch("shutil.disk_usage") as mock_usage:
            # 80% used
            mock_usage.return_value = MagicMock(
                total=100_000_000_000,
                free=20_000_000_000,
            )

            status = manager.check_disk_space(1000)

            assert status.level == "warning"
            assert 0.79 < status.used_pct < 0.81

    def test_check_disk_space_90_percent_critical(
        self, manager: ManifestManager
    ) -> None:
        """Test check_disk_space returns critical at 90% usage."""
        with patch("shutil.disk_usage") as mock_usage:
            # 90% used
            mock_usage.return_value = MagicMock(
                total=100_000_000_000,
                free=10_000_000_000,
            )

            status = manager.check_disk_space(1000)

            assert status.level == "critical"
            assert 0.89 < status.used_pct < 0.91

    def test_check_disk_space_95_percent_blocked(
        self, manager: ManifestManager
    ) -> None:
        """Test check_disk_space raises at 95% usage."""
        with patch("shutil.disk_usage") as mock_usage:
            # 95% used
            mock_usage.return_value = MagicMock(
                total=100_000_000_000,
                free=5_000_000_000,
            )

            with pytest.raises(DiskSpaceError) as exc_info:
                manager.check_disk_space(1000)

            assert "blocked" in str(exc_info.value).lower()

    def test_save_manifest_fails_without_lock(
        self, manager: ManifestManager, temp_dirs: dict[str, Path]
    ) -> None:
        """Test save_manifest fails when lock is not held."""
        manifest = SyncManifest(
            dataset="test_dataset",
            sync_timestamp=datetime.now(UTC),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            row_count=100,
            checksum="abc123" * 10,
            schema_version="v1.0.0",
            wrds_query_hash="def456" * 10,
            file_paths=["test.parquet"],
            validation_status="passed",
        )

        # Token with non-existent lock file
        token = LockToken(
            pid=os.getpid(),
            hostname="test-host",
            writer_id="test-writer",
            acquired_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=4),
            lock_path=temp_dirs["locks"] / "nonexistent.lock",
        )

        with pytest.raises(LockNotHeldError):
            manager.save_manifest(manifest, token)

    def test_save_manifest_updates_version_and_checksum(
        self,
        manager: ManifestManager,
        valid_lock_token: LockToken,
        tmp_path: Path,
    ) -> None:
        """Test save_manifest increments version and updates previous_checksum."""
        # Create actual test file
        test_file = tmp_path / "test.parquet"
        test_file.write_text("test content")

        # Save first manifest
        manifest1 = SyncManifest(
            dataset="test_dataset",
            sync_timestamp=datetime.now(UTC),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            row_count=100,
            checksum="first_checksum_abc123",
            schema_version="v1.0.0",
            wrds_query_hash="def456" * 10,
            file_paths=[str(test_file)],
            validation_status="passed",
        )

        # Update lock path to match dataset
        valid_lock_token.lock_path = (
            valid_lock_token.lock_path.parent / "test_dataset.lock"
        )
        with open(valid_lock_token.lock_path, "w") as f:
            json.dump(valid_lock_token.to_dict(), f)

        manager.save_manifest(manifest1, valid_lock_token)

        # Save second manifest
        manifest2 = SyncManifest(
            dataset="test_dataset",
            sync_timestamp=datetime.now(UTC),
            start_date=date(2024, 2, 1),
            end_date=date(2024, 2, 28),
            row_count=200,
            checksum="second_checksum_def456",
            schema_version="v1.0.0",
            wrds_query_hash="def456" * 10,
            file_paths=[str(test_file)],
            validation_status="passed",
        )

        manager.save_manifest(manifest2, valid_lock_token)

        # Load and verify
        loaded = manager.load_manifest("test_dataset")

        assert loaded is not None
        assert loaded.manifest_version == 2
        assert loaded.previous_checksum == "first_checksum_abc123"

    def test_save_manifest_rejects_cross_dataset_lock(
        self,
        manager: ManifestManager,
        valid_lock_token: LockToken,
    ) -> None:
        """Test save_manifest rejects lock acquired for different dataset.

        This prevents a caller from acquiring a lock for dataset A
        and then using it to write to dataset B's manifest.
        """
        # Lock is for "dataset_a"
        valid_lock_token.lock_path = (
            valid_lock_token.lock_path.parent / "dataset_a.lock"
        )
        with open(valid_lock_token.lock_path, "w") as f:
            json.dump(valid_lock_token.to_dict(), f)

        # But manifest is for "dataset_b"
        manifest = SyncManifest(
            dataset="dataset_b",  # Different from lock
            sync_timestamp=datetime.now(UTC),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            row_count=100,
            checksum="test_checksum",
            schema_version="v1.0.0",
            wrds_query_hash="def456" * 10,
            file_paths=["test.parquet"],
            validation_status="passed",
        )

        # Should reject because lock is for wrong dataset
        with pytest.raises(LockNotHeldError) as exc_info:
            manager.save_manifest(manifest, valid_lock_token)

        assert "Lock path mismatch" in str(exc_info.value)
        assert "dataset_a" in str(exc_info.value)
        assert "dataset_b" in str(exc_info.value)

    def test_rollback_on_failure_restores_previous(
        self,
        manager: ManifestManager,
        valid_lock_token: LockToken,
        tmp_path: Path,
    ) -> None:
        """Test rollback_on_failure restores previous manifest version."""
        # Create actual test file
        test_file = tmp_path / "test.parquet"
        test_file.write_text("test content")

        # Update lock path
        valid_lock_token.lock_path = (
            valid_lock_token.lock_path.parent / "rollback_test.lock"
        )
        with open(valid_lock_token.lock_path, "w") as f:
            json.dump(valid_lock_token.to_dict(), f)

        # Save first manifest
        manifest1 = SyncManifest(
            dataset="rollback_test",
            sync_timestamp=datetime.now(UTC),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            row_count=100,
            checksum="original_checksum",
            schema_version="v1.0.0",
            wrds_query_hash="def456" * 10,
            file_paths=[str(test_file)],
            validation_status="passed",
        )
        manager.save_manifest(manifest1, valid_lock_token)

        # Save second manifest
        manifest2 = SyncManifest(
            dataset="rollback_test",
            sync_timestamp=datetime.now(UTC),
            start_date=date(2024, 2, 1),
            end_date=date(2024, 2, 28),
            row_count=200,
            checksum="updated_checksum",
            schema_version="v1.0.0",
            wrds_query_hash="def456" * 10,
            file_paths=[str(test_file)],
            validation_status="passed",
        )
        manager.save_manifest(manifest2, valid_lock_token)

        # Rollback (requires lock)
        restored = manager.rollback_on_failure("rollback_test", valid_lock_token)

        assert restored is not None
        assert restored.checksum == "original_checksum"
        assert restored.manifest_version == 1

    def test_rollback_returns_none_when_no_previous(
        self,
        manager: ManifestManager,
        valid_lock_token: LockToken,
        tmp_path: Path,
    ) -> None:
        """Test rollback_on_failure returns None when no previous version."""
        # Create actual test file
        test_file = tmp_path / "test.parquet"
        test_file.write_text("test content")

        # Update lock path
        valid_lock_token.lock_path = (
            valid_lock_token.lock_path.parent / "no_previous.lock"
        )
        with open(valid_lock_token.lock_path, "w") as f:
            json.dump(valid_lock_token.to_dict(), f)

        # Save only one manifest
        manifest = SyncManifest(
            dataset="no_previous",
            sync_timestamp=datetime.now(UTC),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            row_count=100,
            checksum="only_checksum",
            schema_version="v1.0.0",
            wrds_query_hash="def456" * 10,
            file_paths=[str(test_file)],
            validation_status="passed",
        )
        manager.save_manifest(manifest, valid_lock_token)

        # Try rollback (requires lock)
        result = manager.rollback_on_failure("no_previous", valid_lock_token)

        assert result is None

    def test_list_manifests_ordered_by_dataset(
        self,
        manager: ManifestManager,
        valid_lock_token: LockToken,
        tmp_path: Path,
    ) -> None:
        """Test list_manifests returns manifests ordered by dataset name."""
        # Create actual test file
        test_file = tmp_path / "test.parquet"
        test_file.write_text("test content")

        datasets = ["zebra", "alpha", "mango"]

        for dataset in datasets:
            lock_path = valid_lock_token.lock_path.parent / f"{dataset}.lock"
            with open(lock_path, "w") as f:
                json.dump({
                    "pid": valid_lock_token.pid,
                    "hostname": valid_lock_token.hostname,
                    "writer_id": valid_lock_token.writer_id,
                    "acquired_at": valid_lock_token.acquired_at.isoformat(),
                    "expires_at": valid_lock_token.expires_at.isoformat(),
                }, f)

            token = LockToken(
                pid=valid_lock_token.pid,
                hostname=valid_lock_token.hostname,
                writer_id=valid_lock_token.writer_id,
                acquired_at=valid_lock_token.acquired_at,
                expires_at=valid_lock_token.expires_at,
                lock_path=lock_path,
            )

            manifest = SyncManifest(
                dataset=dataset,
                sync_timestamp=datetime.now(UTC),
                start_date=date(2024, 1, 1),
                end_date=date(2024, 1, 31),
                row_count=100,
                checksum=f"{dataset}_checksum",
                schema_version="v1.0.0",
                wrds_query_hash="def456" * 10,
                file_paths=[str(test_file)],
                validation_status="passed",
            )
            manager.save_manifest(manifest, token)

        manifests = manager.list_manifests()

        assert len(manifests) == 3
        assert [m.dataset for m in manifests] == ["alpha", "mango", "zebra"]

    def test_create_snapshot_creates_immutable_copy(
        self,
        manager: ManifestManager,
        valid_lock_token: LockToken,
        tmp_path: Path,
    ) -> None:
        """Test create_snapshot creates copy of all manifests."""
        # Create actual test file
        test_file = tmp_path / "test.parquet"
        test_file.write_text("test content")

        # Update lock path
        valid_lock_token.lock_path = (
            valid_lock_token.lock_path.parent / "snapshot_test.lock"
        )
        with open(valid_lock_token.lock_path, "w") as f:
            json.dump(valid_lock_token.to_dict(), f)

        # Create manifest
        manifest = SyncManifest(
            dataset="snapshot_test",
            sync_timestamp=datetime.now(UTC),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            row_count=100,
            checksum="snapshot_checksum",
            schema_version="v1.0.0",
            wrds_query_hash="def456" * 10,
            file_paths=[str(test_file)],
            validation_status="passed",
        )
        manager.save_manifest(manifest, valid_lock_token)

        # Create snapshot
        manager.create_snapshot("2024-01-15")

        # Verify snapshot exists
        snapshot_dir = manager.storage_path / "snapshots" / "2024-01-15"
        assert snapshot_dir.exists()
        assert (snapshot_dir / "snapshot_test.json").exists()

    def test_create_snapshot_fails_on_duplicate(
        self, manager: ManifestManager
    ) -> None:
        """Test create_snapshot fails when snapshot already exists."""
        # Create first snapshot
        manager.create_snapshot("duplicate-tag")

        # Try to create duplicate
        with pytest.raises(ValueError, match="already exists"):
            manager.create_snapshot("duplicate-tag")

    def test_quarantine_data_moves_to_correct_path(
        self,
        manager: ManifestManager,
        valid_lock_token: LockToken,
    ) -> None:
        """Test quarantine_data creates quarantine directory correctly."""
        # Update lock path
        valid_lock_token.lock_path = (
            valid_lock_token.lock_path.parent / "quarantine_test.lock"
        )
        with open(valid_lock_token.lock_path, "w") as f:
            json.dump(valid_lock_token.to_dict(), f)

        manifest = SyncManifest(
            dataset="quarantine_test",
            sync_timestamp=datetime.now(UTC),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            row_count=100,
            checksum="quarantine_checksum",
            schema_version="v1.0.0",
            wrds_query_hash="def456" * 10,
            file_paths=["test.parquet"],
            validation_status="failed",
        )

        quarantine_path = manager.quarantine_data(
            manifest, "Test quarantine reason", valid_lock_token
        )

        assert Path(quarantine_path).exists()
        assert "quarantine_test" in quarantine_path
        assert (Path(quarantine_path) / "manifest.json").exists()
        assert (Path(quarantine_path) / "reason.txt").exists()

    def test_quarantine_data_updates_validation_status(
        self,
        manager: ManifestManager,
        valid_lock_token: LockToken,
    ) -> None:
        """Test quarantine_data updates manifest validation_status."""
        # Update lock path
        valid_lock_token.lock_path = (
            valid_lock_token.lock_path.parent / "status_test.lock"
        )
        with open(valid_lock_token.lock_path, "w") as f:
            json.dump(valid_lock_token.to_dict(), f)

        manifest = SyncManifest(
            dataset="status_test",
            sync_timestamp=datetime.now(UTC),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            row_count=100,
            checksum="status_checksum",
            schema_version="v1.0.0",
            wrds_query_hash="def456" * 10,
            file_paths=["test.parquet"],
            validation_status="failed",
        )

        quarantine_path = manager.quarantine_data(
            manifest, "Status update test", valid_lock_token
        )

        # Load quarantined manifest
        with open(Path(quarantine_path) / "manifest.json") as f:
            quarantined = json.load(f)

        assert quarantined["validation_status"] == "quarantined"
        assert quarantined["quarantine_path"] == quarantine_path

    def test_quarantine_data_moves_parquet_files(
        self,
        manager: ManifestManager,
        valid_lock_token: LockToken,
    ) -> None:
        """Test quarantine_data actually moves parquet files to quarantine."""
        # Update lock path
        valid_lock_token.lock_path = (
            valid_lock_token.lock_path.parent / "parquet_test.lock"
        )
        with open(valid_lock_token.lock_path, "w") as f:
            json.dump(valid_lock_token.to_dict(), f)

        # Create actual parquet files
        data_dir = manager.storage_path.parent / "test_data"
        data_dir.mkdir(parents=True, exist_ok=True)

        file1 = data_dir / "test1.parquet"
        file2 = data_dir / "test2.parquet"
        file1.write_text("parquet content 1")
        file2.write_text("parquet content 2")

        manifest = SyncManifest(
            dataset="parquet_test",
            sync_timestamp=datetime.now(UTC),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            row_count=100,
            checksum="parquet_checksum",
            schema_version="v1.0.0",
            wrds_query_hash="def456" * 10,
            file_paths=[str(file1), str(file2)],
            validation_status="failed",
        )

        quarantine_path = manager.quarantine_data(
            manifest, "Parquet move test", valid_lock_token
        )

        # Verify original files are gone
        assert not file1.exists(), "Original file1 should be moved"
        assert not file2.exists(), "Original file2 should be moved"

        # Verify files are in quarantine/data subdirectory
        # Files now have hash prefix like {hash}_test1.parquet to prevent collisions
        quarantine_data_dir = Path(quarantine_path) / "data"
        assert quarantine_data_dir.exists()

        # List files and verify they end with expected basenames
        quarantine_files = list(quarantine_data_dir.iterdir())
        assert len(quarantine_files) == 2

        # Find files by their original basename suffix
        file1_quarantine = [f for f in quarantine_files if f.name.endswith("_test1.parquet")]
        file2_quarantine = [f for f in quarantine_files if f.name.endswith("_test2.parquet")]
        assert len(file1_quarantine) == 1, "Should have one file ending with _test1.parquet"
        assert len(file2_quarantine) == 1, "Should have one file ending with _test2.parquet"

        # Verify file contents preserved
        assert file1_quarantine[0].read_text() == "parquet content 1"
        assert file2_quarantine[0].read_text() == "parquet content 2"

        # Verify reason file includes original paths
        reason_content = (Path(quarantine_path) / "reason.txt").read_text()
        assert str(file1) in reason_content
        assert str(file2) in reason_content
