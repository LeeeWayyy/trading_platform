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

from libs.data.data_quality.exceptions import DiskSpaceError, LockNotHeldError
from libs.data.data_quality.manifest import ManifestManager, SyncManifest
from libs.data.data_quality.types import LockToken


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
        data = self._create_valid_manifest(sync_timestamp=datetime.now(UTC))
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
        data = self._create_valid_manifest(file_paths=["file1.parquet", "file2.parquet"])
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

    def test_load_manifest_returns_none_if_not_found(self, manager: ManifestManager) -> None:
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

    def test_check_disk_space_80_percent_warning(self, manager: ManifestManager) -> None:
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

    def test_check_disk_space_90_percent_critical(self, manager: ManifestManager) -> None:
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

    def test_check_disk_space_95_percent_blocked(self, manager: ManifestManager) -> None:
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
        valid_lock_token.lock_path = valid_lock_token.lock_path.parent / "test_dataset.lock"
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
        valid_lock_token.lock_path = valid_lock_token.lock_path.parent / "dataset_a.lock"
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
        valid_lock_token.lock_path = valid_lock_token.lock_path.parent / "rollback_test.lock"
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
        valid_lock_token.lock_path = valid_lock_token.lock_path.parent / "no_previous.lock"
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
                json.dump(
                    {
                        "pid": valid_lock_token.pid,
                        "hostname": valid_lock_token.hostname,
                        "writer_id": valid_lock_token.writer_id,
                        "acquired_at": valid_lock_token.acquired_at.isoformat(),
                        "expires_at": valid_lock_token.expires_at.isoformat(),
                    },
                    f,
                )

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
        valid_lock_token.lock_path = valid_lock_token.lock_path.parent / "snapshot_test.lock"
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

    def test_create_snapshot_fails_on_duplicate(self, manager: ManifestManager) -> None:
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
        valid_lock_token.lock_path = valid_lock_token.lock_path.parent / "quarantine_test.lock"
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
        valid_lock_token.lock_path = valid_lock_token.lock_path.parent / "status_test.lock"
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

        quarantine_path = manager.quarantine_data(manifest, "Status update test", valid_lock_token)

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
        valid_lock_token.lock_path = valid_lock_token.lock_path.parent / "parquet_test.lock"
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

        quarantine_path = manager.quarantine_data(manifest, "Parquet move test", valid_lock_token)

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

    def test_acquire_lock_and_context_manager(
        self,
        manager: ManifestManager,
    ) -> None:
        """Test acquire_lock context manager acquires and releases lock."""
        dataset = "lock_test"

        with manager.acquire_lock(dataset, "test-writer") as token:
            # Lock should be held
            assert token.lock_path.exists()
            assert token.writer_id == "test-writer"

        # Lock should be released after context manager exits
        lock_path = manager._lock_path(dataset)
        assert not lock_path.exists()

    def test_acquire_lock_timeout(
        self,
        manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test acquire_lock raises LockNotHeldError on timeout."""
        dataset = "timeout_test"
        lock_path = manager._lock_path(dataset)

        # Create lock file held by another process
        lock_data = {
            "pid": 99999,  # Different PID (non-existent process)
            "hostname": "other-host",  # Different host to prevent stale lock breaking
            "writer_id": "blocking-writer",
            "acquired_at": datetime.now(UTC).isoformat(),
            "expires_at": (datetime.now(UTC) + timedelta(hours=4)).isoformat(),
        }
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "w") as f:
            json.dump(lock_data, f)

        with pytest.raises(LockNotHeldError) as exc_info:
            with manager.acquire_lock(dataset, "test-writer", timeout_seconds=0.3):
                pass

        assert "Failed to acquire lock" in str(exc_info.value)

    def test_try_break_stale_lock_hard_timeout(
        self,
        manager: ManifestManager,
    ) -> None:
        """Test _try_break_stale_lock removes lock after hard timeout (30 min)."""
        import time

        dataset = "stale_test"
        lock_path = manager._lock_path(dataset)

        # Create lock file
        lock_data = {
            "pid": 99999,
            "hostname": "other-host",
            "writer_id": "old-writer",
            "acquired_at": datetime.now(UTC).isoformat(),
            "expires_at": (datetime.now(UTC) + timedelta(hours=4)).isoformat(),
        }
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "w") as f:
            json.dump(lock_data, f)

        # Set mtime to be older than hard timeout (30 min = 1800 sec)
        old_time = time.time() - 1900  # 31+ minutes ago
        os.utime(lock_path, (old_time, old_time))

        # Should break the stale lock
        manager._try_break_stale_lock(lock_path, dataset)

        assert not lock_path.exists()

    def test_try_break_stale_lock_dead_process(
        self,
        manager: ManifestManager,
    ) -> None:
        """Test _try_break_stale_lock removes lock when owner process is dead."""
        import socket
        import time

        dataset = "dead_process_test"
        lock_path = manager._lock_path(dataset)
        current_hostname = socket.gethostname()

        # Create lock file with current hostname but non-existent PID
        lock_data = {
            "pid": 99999999,  # Non-existent PID
            "hostname": current_hostname,  # Same host
            "writer_id": "dead-writer",
            "acquired_at": datetime.now(UTC).isoformat(),
            "expires_at": (datetime.now(UTC) + timedelta(hours=4)).isoformat(),
        }
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "w") as f:
            json.dump(lock_data, f)

        # Set mtime to be older than soft timeout (5 min = 300 sec)
        old_time = time.time() - 350  # 5+ minutes ago
        os.utime(lock_path, (old_time, old_time))

        # Should break the stale lock since process is dead
        manager._try_break_stale_lock(lock_path, dataset)

        assert not lock_path.exists()

    def test_try_break_stale_lock_remote_host_waits(
        self,
        manager: ManifestManager,
    ) -> None:
        """Test _try_break_stale_lock waits for hard timeout on remote host locks."""
        import time

        dataset = "remote_host_test"
        lock_path = manager._lock_path(dataset)

        # Create lock file with different hostname
        lock_data = {
            "pid": 12345,
            "hostname": "remote-host-xyz",  # Different host
            "writer_id": "remote-writer",
            "acquired_at": datetime.now(UTC).isoformat(),
            "expires_at": (datetime.now(UTC) + timedelta(hours=4)).isoformat(),
        }
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "w") as f:
            json.dump(lock_data, f)

        # Set mtime older than soft timeout but not hard timeout
        old_time = time.time() - 400  # 6+ minutes ago (soft timeout exceeded)
        os.utime(lock_path, (old_time, old_time))

        # Should NOT break the lock (remote host, wait for hard timeout)
        manager._try_break_stale_lock(lock_path, dataset)

        # Lock should still exist
        assert lock_path.exists()

    def test_try_break_stale_lock_unreadable_lock(
        self,
        manager: ManifestManager,
    ) -> None:
        """Test _try_break_stale_lock removes unreadable lock file."""
        import time

        dataset = "unreadable_test"
        lock_path = manager._lock_path(dataset)

        # Create invalid JSON lock file
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "w") as f:
            f.write("invalid json content{{{")

        # Set mtime older than soft timeout
        old_time = time.time() - 350
        os.utime(lock_path, (old_time, old_time))

        # Should remove the unreadable lock
        manager._try_break_stale_lock(lock_path, dataset)

        assert not lock_path.exists()

    def test_try_break_stale_lock_nonexistent(
        self,
        manager: ManifestManager,
    ) -> None:
        """Test _try_break_stale_lock handles non-existent lock file."""
        dataset = "nonexistent_test"
        lock_path = manager._lock_path(dataset)

        # Should not raise, just return
        manager._try_break_stale_lock(lock_path, dataset)

    def test_release_lock_ownership_verification(
        self,
        manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test _release_lock only releases if we own the lock."""
        lock_path = temp_dirs["locks"] / "owned_test.lock"

        # Our lock data
        our_lock_data = {
            "pid": os.getpid(),
            "hostname": "our-host",
            "writer_id": "our-writer",
            "acquired_at": datetime.now(UTC).isoformat(),
            "expires_at": (datetime.now(UTC) + timedelta(hours=4)).isoformat(),
        }

        # Write lock file with OUR data
        with open(lock_path, "w") as f:
            json.dump(our_lock_data, f)

        # Try to release with matching data - should succeed
        manager._release_lock(lock_path, our_lock_data, "owned_test")

        # Lock should be deleted
        assert not lock_path.exists()

    def test_release_lock_different_owner(
        self,
        manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test _release_lock does not release lock owned by another process."""
        lock_path = temp_dirs["locks"] / "other_owner_test.lock"

        # File has different owner
        file_lock_data = {
            "pid": 99999,
            "hostname": "other-host",
            "writer_id": "other-writer",
            "acquired_at": datetime.now(UTC).isoformat(),
            "expires_at": (datetime.now(UTC) + timedelta(hours=4)).isoformat(),
        }
        with open(lock_path, "w") as f:
            json.dump(file_lock_data, f)

        # Our lock data
        our_lock_data = {
            "pid": os.getpid(),
            "hostname": "our-host",
            "writer_id": "our-writer",
            "acquired_at": datetime.now(UTC).isoformat(),
            "expires_at": (datetime.now(UTC) + timedelta(hours=4)).isoformat(),
        }

        # Try to release - should NOT delete (different owner)
        manager._release_lock(lock_path, our_lock_data, "other_owner_test")

        # Lock should still exist
        assert lock_path.exists()

    def test_release_lock_nonexistent(
        self,
        manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test _release_lock handles non-existent lock file gracefully."""
        lock_path = temp_dirs["locks"] / "nonexistent.lock"
        lock_data = {"pid": os.getpid(), "hostname": "test", "writer_id": "test"}

        # Should not raise
        manager._release_lock(lock_path, lock_data, "nonexistent")

    def test_assert_lock_held_fails_when_lock_missing(
        self,
        manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test assert_lock_held fails when lock file doesn't exist."""
        lock_path = temp_dirs["locks"] / "missing.lock"
        token = LockToken(
            pid=os.getpid(),
            hostname="test",
            writer_id="test",
            acquired_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=4),
            lock_path=lock_path,
        )

        with pytest.raises(LockNotHeldError) as exc_info:
            manager.assert_lock_held(token)

        assert "does not exist" in str(exc_info.value)

    def test_assert_lock_held_fails_with_unreadable_lock(
        self,
        manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test assert_lock_held fails with corrupt/unreadable lock file."""
        lock_path = temp_dirs["locks"] / "corrupt.lock"

        # Create invalid JSON
        with open(lock_path, "w") as f:
            f.write("not valid json{{{")

        token = LockToken(
            pid=os.getpid(),
            hostname="test",
            writer_id="test",
            acquired_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=4),
            lock_path=lock_path,
        )

        with pytest.raises(LockNotHeldError) as exc_info:
            manager.assert_lock_held(token)

        assert "Failed to read" in str(exc_info.value)

    def test_assert_lock_held_fails_hostname_mismatch(
        self,
        manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test assert_lock_held fails when hostname doesn't match."""
        lock_path = temp_dirs["locks"] / "hostname_test.lock"

        lock_data = {
            "pid": os.getpid(),
            "hostname": "file-hostname",  # Different hostname in file
            "writer_id": "test",
            "acquired_at": datetime.now(UTC).isoformat(),
            "expires_at": (datetime.now(UTC) + timedelta(hours=4)).isoformat(),
        }
        with open(lock_path, "w") as f:
            json.dump(lock_data, f)

        token = LockToken(
            pid=os.getpid(),
            hostname="token-hostname",  # Different hostname in token
            writer_id="test",
            acquired_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=4),
            lock_path=lock_path,
        )

        with pytest.raises(LockNotHeldError) as exc_info:
            manager.assert_lock_held(token)

        assert "hostname mismatch" in str(exc_info.value).lower()

    def test_assert_lock_held_fails_writer_id_mismatch(
        self,
        manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test assert_lock_held fails when writer_id doesn't match."""
        lock_path = temp_dirs["locks"] / "writer_test.lock"

        lock_data = {
            "pid": os.getpid(),
            "hostname": "test-host",
            "writer_id": "file-writer",  # Different writer_id in file
            "acquired_at": datetime.now(UTC).isoformat(),
            "expires_at": (datetime.now(UTC) + timedelta(hours=4)).isoformat(),
        }
        with open(lock_path, "w") as f:
            json.dump(lock_data, f)

        token = LockToken(
            pid=os.getpid(),
            hostname="test-host",
            writer_id="token-writer",  # Different writer_id in token
            acquired_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=4),
            lock_path=lock_path,
        )

        with pytest.raises(LockNotHeldError) as exc_info:
            manager.assert_lock_held(token)

        assert "writer_id mismatch" in str(exc_info.value).lower()

    def test_assert_lock_held_hard_timeout_exceeded(
        self,
        manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test assert_lock_held fails when hard timeout exceeded."""
        lock_path = temp_dirs["locks"] / "hard_timeout.lock"

        # Lock acquired 31+ minutes ago (exceeds 30 min hard timeout)
        acquired_at = datetime.now(UTC) - timedelta(minutes=35)
        lock_data = {
            "pid": os.getpid(),
            "hostname": "test-host",
            "writer_id": "test-writer",
            "acquired_at": acquired_at.isoformat(),
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),  # Not expired
        }
        with open(lock_path, "w") as f:
            json.dump(lock_data, f)

        token = LockToken(
            pid=os.getpid(),
            hostname="test-host",
            writer_id="test-writer",
            acquired_at=acquired_at,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            lock_path=lock_path,
        )

        with pytest.raises(LockNotHeldError) as exc_info:
            manager.assert_lock_held(token)

        assert "hard timeout" in str(exc_info.value).lower()

    def test_assert_lock_held_stale_lock_warning(
        self,
        manager: ManifestManager,
        temp_dirs: dict[str, Path],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test assert_lock_held logs warning for stale but valid lock."""
        import logging
        import time

        lock_path = temp_dirs["locks"] / "stale_warning.lock"

        acquired_at = datetime.now(UTC)
        lock_data = {
            "pid": os.getpid(),
            "hostname": "test-host",
            "writer_id": "test-writer",
            "acquired_at": acquired_at.isoformat(),
            "expires_at": (datetime.now(UTC) + timedelta(hours=4)).isoformat(),
        }
        with open(lock_path, "w") as f:
            json.dump(lock_data, f)

        # Set mtime to 6 minutes ago (stale but within hard timeout)
        old_time = time.time() - 360  # 6 minutes
        os.utime(lock_path, (old_time, old_time))

        token = LockToken(
            pid=os.getpid(),
            hostname="test-host",
            writer_id="test-writer",
            acquired_at=acquired_at,
            expires_at=datetime.now(UTC) + timedelta(hours=4),
            lock_path=lock_path,
        )

        with caplog.at_level(logging.WARNING):
            manager.assert_lock_held(token)

        assert "stale" in caplog.text.lower()

    def test_refresh_lock_success(
        self,
        manager: ManifestManager,
        valid_lock_token: LockToken,
    ) -> None:
        """Test refresh_lock extends expiration."""
        # Valid lock token already created by fixture
        original_expires_at = valid_lock_token.expires_at

        updated_token = manager.refresh_lock(valid_lock_token)

        assert updated_token.expires_at > original_expires_at

    def test_refresh_lock_fails_hard_timeout(
        self,
        manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test refresh_lock fails when hard timeout exceeded."""
        lock_path = temp_dirs["locks"] / "refresh_hard.lock"

        # Lock acquired 31+ minutes ago
        acquired_at = datetime.now(UTC) - timedelta(minutes=35)
        lock_data = {
            "pid": os.getpid(),
            "hostname": "test-host",
            "writer_id": "test-writer",
            "acquired_at": acquired_at.isoformat(),
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        }
        with open(lock_path, "w") as f:
            json.dump(lock_data, f)

        token = LockToken(
            pid=os.getpid(),
            hostname="test-host",
            writer_id="test-writer",
            acquired_at=acquired_at,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            lock_path=lock_path,
        )

        with pytest.raises(LockNotHeldError) as exc_info:
            manager.refresh_lock(token)

        assert "hard timeout" in str(exc_info.value).lower()

    def test_refresh_lock_fails_lock_not_held(
        self,
        manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test refresh_lock fails when lock file doesn't exist."""
        lock_path = temp_dirs["locks"] / "missing_refresh.lock"

        token = LockToken(
            pid=os.getpid(),
            hostname="test-host",
            writer_id="test-writer",
            acquired_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=4),
            lock_path=lock_path,
        )

        with pytest.raises(LockNotHeldError):
            manager.refresh_lock(token)

    def test_check_disk_space_ok(self, manager: ManifestManager) -> None:
        """Test check_disk_space returns OK when usage is low."""
        with patch("shutil.disk_usage") as mock_usage:
            # 50% used
            mock_usage.return_value = MagicMock(
                total=100_000_000_000,
                free=50_000_000_000,
            )

            status = manager.check_disk_space(1000)

            assert status.level == "ok"
            assert 0.49 < status.used_pct < 0.51

    def test_check_disk_space_insufficient(self, manager: ManifestManager) -> None:
        """Test check_disk_space raises when free space < required."""
        with patch("shutil.disk_usage") as mock_usage:
            # Low usage percentage (50%) but not enough free bytes for the required amount
            # total=100_000, free=50_000 => 50% used (below 95% blocked threshold)
            # But free (50_000) < required (100_000)
            mock_usage.return_value = MagicMock(
                total=100_000,
                free=50_000,  # 50% used, below block threshold
            )

            with pytest.raises(DiskSpaceError) as exc_info:
                manager.check_disk_space(100_000)  # Need 100_000 bytes, only 50_000 available

            assert "Insufficient disk space" in str(exc_info.value)

    def test_save_manifest_missing_file_raises(
        self,
        manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test save_manifest raises when referenced file doesn't exist."""
        from libs.data.data_quality.exceptions import QuarantineError

        dataset = "missing_file_test"
        lock_path = temp_dirs["locks"] / f"{dataset}.lock"

        # Create lock
        now = datetime.now(UTC)
        lock_data = {
            "pid": os.getpid(),
            "hostname": "test-host",
            "writer_id": "test-writer",
            "acquired_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=4)).isoformat(),
        }
        with open(lock_path, "w") as f:
            json.dump(lock_data, f)

        token = LockToken(
            pid=os.getpid(),
            hostname="test-host",
            writer_id="test-writer",
            acquired_at=now,
            expires_at=now + timedelta(hours=4),
            lock_path=lock_path,
        )

        manifest = SyncManifest(
            dataset=dataset,
            sync_timestamp=datetime.now(UTC),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            row_count=100,
            checksum="test_checksum",
            schema_version="v1.0.0",
            wrds_query_hash="test_hash",
            file_paths=["/nonexistent/path/file.parquet"],  # Doesn't exist
            validation_status="passed",
        )

        with pytest.raises(QuarantineError) as exc_info:
            manager.save_manifest(manifest, token)

        assert "does not exist" in str(exc_info.value)

    def test_save_manifest_empty_file_raises(
        self,
        manager: ManifestManager,
        temp_dirs: dict[str, Path],
        tmp_path: Path,
    ) -> None:
        """Test save_manifest raises when referenced file is empty."""
        from libs.data.data_quality.exceptions import QuarantineError

        dataset = "empty_file_test"
        lock_path = temp_dirs["locks"] / f"{dataset}.lock"

        # Create lock
        now = datetime.now(UTC)
        lock_data = {
            "pid": os.getpid(),
            "hostname": "test-host",
            "writer_id": "test-writer",
            "acquired_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=4)).isoformat(),
        }
        with open(lock_path, "w") as f:
            json.dump(lock_data, f)

        token = LockToken(
            pid=os.getpid(),
            hostname="test-host",
            writer_id="test-writer",
            acquired_at=now,
            expires_at=now + timedelta(hours=4),
            lock_path=lock_path,
        )

        # Create empty file
        empty_file = tmp_path / "empty.parquet"
        empty_file.touch()  # Creates empty file

        manifest = SyncManifest(
            dataset=dataset,
            sync_timestamp=datetime.now(UTC),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            row_count=100,
            checksum="test_checksum",
            schema_version="v1.0.0",
            wrds_query_hash="test_hash",
            file_paths=[str(empty_file)],  # Empty file
            validation_status="passed",
        )

        with pytest.raises(QuarantineError) as exc_info:
            manager.save_manifest(manifest, token)

        assert "zero size" in str(exc_info.value)

    def test_save_manifest_disk_full_raises(
        self,
        manager: ManifestManager,
        temp_dirs: dict[str, Path],
        tmp_path: Path,
    ) -> None:
        """Test save_manifest raises DiskSpaceError on ENOSPC."""
        dataset = "disk_full_test"
        lock_path = temp_dirs["locks"] / f"{dataset}.lock"

        # Create lock
        now = datetime.now(UTC)
        lock_data = {
            "pid": os.getpid(),
            "hostname": "test-host",
            "writer_id": "test-writer",
            "acquired_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=4)).isoformat(),
        }
        with open(lock_path, "w") as f:
            json.dump(lock_data, f)

        token = LockToken(
            pid=os.getpid(),
            hostname="test-host",
            writer_id="test-writer",
            acquired_at=now,
            expires_at=now + timedelta(hours=4),
            lock_path=lock_path,
        )

        # Create test file
        test_file = tmp_path / "test.parquet"
        test_file.write_text("content")

        manifest = SyncManifest(
            dataset=dataset,
            sync_timestamp=datetime.now(UTC),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            row_count=100,
            checksum="test_checksum",
            schema_version="v1.0.0",
            wrds_query_hash="test_hash",
            file_paths=[str(test_file)],
            validation_status="passed",
        )

        # Mock _atomic_write to raise ENOSPC
        with patch.object(manager, "_atomic_write") as mock_write:
            enospc_error = OSError(28, "No space left on device")
            enospc_error.errno = 28
            mock_write.side_effect = enospc_error

            with pytest.raises(DiskSpaceError) as exc_info:
                manager.save_manifest(manifest, token)

            assert "Disk full" in str(exc_info.value)

    def test_rollback_fails_no_manifest(
        self,
        manager: ManifestManager,
        valid_lock_token: LockToken,
    ) -> None:
        """Test rollback_on_failure returns None when no manifest exists."""
        dataset = "nonexistent_rollback"
        valid_lock_token.lock_path = valid_lock_token.lock_path.parent / f"{dataset}.lock"
        with open(valid_lock_token.lock_path, "w") as f:
            json.dump(valid_lock_token.to_dict(), f)

        result = manager.rollback_on_failure(dataset, valid_lock_token)

        assert result is None

    def test_rollback_fails_backup_not_found(
        self,
        manager: ManifestManager,
        valid_lock_token: LockToken,
        tmp_path: Path,
    ) -> None:
        """Test rollback returns None when backup file is missing."""
        dataset = "backup_missing"
        valid_lock_token.lock_path = valid_lock_token.lock_path.parent / f"{dataset}.lock"
        with open(valid_lock_token.lock_path, "w") as f:
            json.dump(valid_lock_token.to_dict(), f)

        # Create test file
        test_file = tmp_path / "test.parquet"
        test_file.write_text("content")

        # Save manifest with previous_checksum set (indicating backup should exist)
        manifest = SyncManifest(
            dataset=dataset,
            sync_timestamp=datetime.now(UTC),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            row_count=100,
            checksum="current_checksum",
            schema_version="v1.0.0",
            wrds_query_hash="test_hash",
            file_paths=[str(test_file)],
            validation_status="passed",
            manifest_version=2,
            previous_checksum="previous_checksum",
        )

        # Write manifest directly (bypassing normal save to skip backup creation)
        manifest_path = manager._manifest_path(dataset)
        with open(manifest_path, "w") as f:
            json.dump(manifest.model_dump(), f, default=str)

        # Rollback should fail - no backup file
        result = manager.rollback_on_failure(dataset, valid_lock_token)

        assert result is None

    def test_rollback_fails_checksum_mismatch(
        self,
        manager: ManifestManager,
        valid_lock_token: LockToken,
        tmp_path: Path,
    ) -> None:
        """Test rollback returns None when backup checksum doesn't match."""
        dataset = "checksum_mismatch"
        valid_lock_token.lock_path = valid_lock_token.lock_path.parent / f"{dataset}.lock"
        with open(valid_lock_token.lock_path, "w") as f:
            json.dump(valid_lock_token.to_dict(), f)

        test_file = tmp_path / "test.parquet"
        test_file.write_text("content")

        # Create a backup file with wrong checksum
        backup_path = manager._backup_path(dataset, 1)
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        backup_manifest = {
            "dataset": dataset,
            "sync_timestamp": datetime.now(UTC).isoformat(),
            "start_date": "2024-01-01",
            "end_date": "2024-01-31",
            "row_count": 100,
            "checksum": "wrong_checksum",  # Different from previous_checksum
            "schema_version": "v1.0.0",
            "wrds_query_hash": "test_hash",
            "file_paths": [str(test_file)],
            "validation_status": "passed",
            "manifest_version": 1,
        }
        with open(backup_path, "w") as f:
            json.dump(backup_manifest, f)

        # Create current manifest expecting "previous_checksum"
        manifest = SyncManifest(
            dataset=dataset,
            sync_timestamp=datetime.now(UTC),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            row_count=100,
            checksum="current_checksum",
            schema_version="v1.0.0",
            wrds_query_hash="test_hash",
            file_paths=[str(test_file)],
            validation_status="passed",
            manifest_version=2,
            previous_checksum="previous_checksum",  # Expected, but backup has "wrong_checksum"
        )
        manifest_path = manager._manifest_path(dataset)
        with open(manifest_path, "w") as f:
            json.dump(manifest.model_dump(), f, default=str)

        result = manager.rollback_on_failure(dataset, valid_lock_token)

        assert result is None

    def test_rollback_rejects_cross_dataset_lock(
        self,
        manager: ManifestManager,
        valid_lock_token: LockToken,
        tmp_path: Path,
    ) -> None:
        """Test rollback rejects lock for different dataset."""
        # Lock is for dataset_a
        valid_lock_token.lock_path = valid_lock_token.lock_path.parent / "dataset_a.lock"
        with open(valid_lock_token.lock_path, "w") as f:
            json.dump(valid_lock_token.to_dict(), f)

        # But trying to rollback dataset_b
        with pytest.raises(LockNotHeldError) as exc_info:
            manager.rollback_on_failure("dataset_b", valid_lock_token)

        assert "Lock path mismatch" in str(exc_info.value)

    def test_quarantine_rejects_cross_dataset_lock(
        self,
        manager: ManifestManager,
        valid_lock_token: LockToken,
    ) -> None:
        """Test quarantine rejects lock for different dataset."""
        # Lock is for dataset_a
        valid_lock_token.lock_path = valid_lock_token.lock_path.parent / "dataset_a.lock"
        with open(valid_lock_token.lock_path, "w") as f:
            json.dump(valid_lock_token.to_dict(), f)

        manifest = SyncManifest(
            dataset="dataset_b",  # Different from lock
            sync_timestamp=datetime.now(UTC),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            row_count=100,
            checksum="test_checksum",
            schema_version="v1.0.0",
            wrds_query_hash="test_hash",
            file_paths=["test.parquet"],
            validation_status="failed",
        )

        with pytest.raises(LockNotHeldError) as exc_info:
            manager.quarantine_data(manifest, "test reason", valid_lock_token)

        assert "Lock path mismatch" in str(exc_info.value)

    def test_quarantine_security_path_validation(
        self,
        manager: ManifestManager,
        valid_lock_token: LockToken,
        tmp_path: Path,
    ) -> None:
        """Test quarantine rejects files outside data_root."""
        from libs.data.data_quality.exceptions import QuarantineError

        dataset = "security_test"
        valid_lock_token.lock_path = valid_lock_token.lock_path.parent / f"{dataset}.lock"
        with open(valid_lock_token.lock_path, "w") as f:
            json.dump(valid_lock_token.to_dict(), f)

        # Create file outside data_root
        outside_file = Path("/tmp/outside_data_root.parquet")
        outside_file.write_text("malicious content")

        manifest = SyncManifest(
            dataset=dataset,
            sync_timestamp=datetime.now(UTC),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            row_count=100,
            checksum="test_checksum",
            schema_version="v1.0.0",
            wrds_query_hash="test_hash",
            file_paths=[str(outside_file)],
            validation_status="failed",
        )

        try:
            with pytest.raises(QuarantineError) as exc_info:
                manager.quarantine_data(manifest, "security test", valid_lock_token)

            assert "Security violation" in str(exc_info.value)
        finally:
            # Cleanup
            if outside_file.exists():
                outside_file.unlink()

    def test_quarantine_missing_files_recorded(
        self,
        manager: ManifestManager,
        valid_lock_token: LockToken,
    ) -> None:
        """Test quarantine handles and records missing files."""
        dataset = "missing_files_test"
        valid_lock_token.lock_path = valid_lock_token.lock_path.parent / f"{dataset}.lock"
        with open(valid_lock_token.lock_path, "w") as f:
            json.dump(valid_lock_token.to_dict(), f)

        manifest = SyncManifest(
            dataset=dataset,
            sync_timestamp=datetime.now(UTC),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            row_count=100,
            checksum="test_checksum",
            schema_version="v1.0.0",
            wrds_query_hash="test_hash",
            file_paths=["/nonexistent/path1.parquet", "/nonexistent/path2.parquet"],
            validation_status="failed",
        )

        quarantine_path = manager.quarantine_data(manifest, "missing files test", valid_lock_token)

        # Load quarantined manifest
        with open(Path(quarantine_path) / "manifest.json") as f:
            quarantined = json.load(f)

        # Missing files should be prefixed with "MISSING:"
        assert any("MISSING:" in p for p in quarantined["file_paths"])

    def test_quarantine_oserror_cleanup(
        self,
        manager: ManifestManager,
        valid_lock_token: LockToken,
        tmp_path: Path,
    ) -> None:
        """Test quarantine cleans up staging on OSError."""
        from libs.data.data_quality.exceptions import QuarantineError

        dataset = "oserror_test"
        valid_lock_token.lock_path = valid_lock_token.lock_path.parent / f"{dataset}.lock"
        with open(valid_lock_token.lock_path, "w") as f:
            json.dump(valid_lock_token.to_dict(), f)

        # Create test file
        test_file = tmp_path / "test.parquet"
        test_file.write_text("content")

        manifest = SyncManifest(
            dataset=dataset,
            sync_timestamp=datetime.now(UTC),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            row_count=100,
            checksum="test_checksum",
            schema_version="v1.0.0",
            wrds_query_hash="test_hash",
            file_paths=[str(test_file)],
            validation_status="failed",
        )

        # Mock Path.rename to raise OSError
        with patch.object(Path, "rename") as mock_rename:
            mock_rename.side_effect = OSError("Permission denied")

            with pytest.raises(QuarantineError) as exc_info:
                manager.quarantine_data(manifest, "oserror test", valid_lock_token)

            assert "Failed to quarantine" in str(exc_info.value)

    def test_list_manifests_handles_invalid_json(
        self,
        manager: ManifestManager,
        temp_dirs: dict[str, Path],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test list_manifests skips files with invalid JSON."""
        import logging

        # Create invalid JSON manifest file
        invalid_file = temp_dirs["storage"] / "invalid.json"
        with open(invalid_file, "w") as f:
            f.write("not valid json{{{")

        with caplog.at_level(logging.WARNING):
            _ = manager.list_manifests()

        # Should return empty list (or other valid manifests)
        # and log a warning
        assert "Failed to load" in caplog.text

    def test_list_manifests_skips_internal_files(
        self,
        manager: ManifestManager,
        valid_lock_token: LockToken,
        tmp_path: Path,
    ) -> None:
        """Test list_manifests skips files starting with underscore."""
        # Create test file
        test_file = tmp_path / "test.parquet"
        test_file.write_text("content")

        # Create a normal manifest
        dataset = "normal_dataset"
        valid_lock_token.lock_path = valid_lock_token.lock_path.parent / f"{dataset}.lock"
        with open(valid_lock_token.lock_path, "w") as f:
            json.dump(valid_lock_token.to_dict(), f)

        manifest = SyncManifest(
            dataset=dataset,
            sync_timestamp=datetime.now(UTC),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            row_count=100,
            checksum="test_checksum",
            schema_version="v1.0.0",
            wrds_query_hash="test_hash",
            file_paths=[str(test_file)],
            validation_status="passed",
        )
        manager.save_manifest(manifest, valid_lock_token)

        # Create internal file (starts with underscore)
        internal_file = manager.storage_path / "_internal.json"
        with open(internal_file, "w") as f:
            f.write('{"internal": "data"}')

        manifests = manager.list_manifests()

        # Should only find the normal dataset, not the internal file
        assert len(manifests) == 1
        assert manifests[0].dataset == dataset

    def test_sanitize_dataset_prevents_path_traversal(
        self,
        manager: ManifestManager,
    ) -> None:
        """Test _sanitize_dataset extracts basename to prevent path traversal."""
        # Path traversal attempts get sanitized to just the basename
        assert manager._sanitize_dataset("../../../etc/passwd") == "passwd"
        assert manager._sanitize_dataset("/etc/passwd") == "passwd"
        assert manager._sanitize_dataset("subdir/dataset") == "dataset"

    def test_sanitize_dataset_rejects_invalid_names(
        self,
        manager: ManifestManager,
    ) -> None:
        """Test _sanitize_dataset rejects empty or dot-only names."""
        with pytest.raises(ValueError, match="Invalid dataset name"):
            manager._sanitize_dataset("..")

        with pytest.raises(ValueError, match="Invalid dataset name"):
            manager._sanitize_dataset(".")

        with pytest.raises(ValueError, match="Invalid dataset name"):
            manager._sanitize_dataset("")

    def test_validate_file_path_success(
        self,
        manager: ManifestManager,
        tmp_path: Path,
    ) -> None:
        """Test _validate_file_path accepts files within data_root."""
        # Create file within data_root
        valid_file = tmp_path / "valid.parquet"
        valid_file.touch()

        assert manager._validate_file_path(valid_file) is True

    def test_validate_file_path_rejects_outside_root(
        self,
        manager: ManifestManager,
    ) -> None:
        """Test _validate_file_path rejects files outside data_root."""
        outside_file = Path("/etc/passwd")
        assert manager._validate_file_path(outside_file) is False

    def test_atomic_write_oserror_cleanup(
        self,
        manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test _atomic_write cleans up temp file on OSError."""
        path = temp_dirs["storage"] / "oserror_test.json"
        data = {"key": "value"}

        # Mock os.fsync to raise OSError
        with patch("os.fsync") as mock_fsync:
            mock_fsync.side_effect = OSError("I/O error")

            with pytest.raises(OSError, match="I/O error"):
                manager._atomic_write(path, data)

        # Original path should not exist (write failed)
        assert not path.exists()

    def test_atomic_write_typeerror_cleanup(
        self,
        manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test _atomic_write cleans up temp file on TypeError (serialization error)."""
        path = temp_dirs["storage"] / "typeerror_test.json"
        data = {"key": "value"}

        # Mock json.dump to raise TypeError
        with patch("json.dump") as mock_dump:
            mock_dump.side_effect = TypeError("Object of type X is not JSON serializable")

            with pytest.raises(TypeError):
                manager._atomic_write(path, data)

        # Original path should not exist
        assert not path.exists()

    def test_validate_file_path_handles_oserror(
        self,
        manager: ManifestManager,
    ) -> None:
        """Test _validate_file_path returns False on OSError."""
        # Create a path object that raises OSError on resolve
        with patch.object(Path, "resolve") as mock_resolve:
            mock_resolve.side_effect = OSError("Permission denied")
            result = manager._validate_file_path(Path("/some/path"))

        assert result is False

    def test_save_manifest_with_warning_disk_level(
        self,
        manager: ManifestManager,
        valid_lock_token: LockToken,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test save_manifest logs warning when disk is at warning level."""
        import logging

        dataset = "warning_disk_test"
        valid_lock_token.lock_path = valid_lock_token.lock_path.parent / f"{dataset}.lock"
        with open(valid_lock_token.lock_path, "w") as f:
            json.dump(valid_lock_token.to_dict(), f)

        test_file = tmp_path / "test.parquet"
        test_file.write_text("content")

        manifest = SyncManifest(
            dataset=dataset,
            sync_timestamp=datetime.now(UTC),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            row_count=100,
            checksum="test_checksum",
            schema_version="v1.0.0",
            wrds_query_hash="test_hash",
            file_paths=[str(test_file)],
            validation_status="passed",
        )

        # Mock disk usage to return warning level (80%+)
        with patch("shutil.disk_usage") as mock_usage:
            mock_usage.return_value = MagicMock(
                total=100_000_000_000,
                free=19_000_000_000,  # 81% used
            )
            with caplog.at_level(logging.WARNING):
                manager.save_manifest(manifest, valid_lock_token)

        assert "warning" in caplog.text.lower()

    def test_save_manifest_with_critical_disk_level(
        self,
        manager: ManifestManager,
        valid_lock_token: LockToken,
        tmp_path: Path,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Test save_manifest logs critical when disk is at critical level."""
        import logging

        dataset = "critical_disk_test"
        valid_lock_token.lock_path = valid_lock_token.lock_path.parent / f"{dataset}.lock"
        with open(valid_lock_token.lock_path, "w") as f:
            json.dump(valid_lock_token.to_dict(), f)

        test_file = tmp_path / "test.parquet"
        test_file.write_text("content")

        manifest = SyncManifest(
            dataset=dataset,
            sync_timestamp=datetime.now(UTC),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            row_count=100,
            checksum="test_checksum",
            schema_version="v1.0.0",
            wrds_query_hash="test_hash",
            file_paths=[str(test_file)],
            validation_status="passed",
        )

        # Mock disk usage to return critical level (90%+)
        with patch("shutil.disk_usage") as mock_usage:
            mock_usage.return_value = MagicMock(
                total=100_000_000_000,
                free=9_000_000_000,  # 91% used
            )
            with caplog.at_level(logging.CRITICAL):
                manager.save_manifest(manifest, valid_lock_token)

        assert "critical" in caplog.text.lower()

    def test_refresh_lock_fails_when_file_becomes_unreadable(
        self,
        manager: ManifestManager,
        valid_lock_token: LockToken,
    ) -> None:
        """Test refresh_lock fails if lock file becomes unreadable."""
        # First assert_lock_held will pass, then the second read in refresh_lock will fail
        call_count = [0]
        original_open = open

        def mock_open(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:  # Second call in refresh_lock
                raise json.JSONDecodeError("Invalid JSON", "", 0)
            return original_open(*args, **kwargs)

        # This test is complex to set up correctly, so we'll mock assert_lock_held to pass
        # but then have the second file read fail
        with patch.object(manager, "assert_lock_held"):
            with patch("builtins.open") as mock_file:
                mock_file.side_effect = OSError("Permission denied")

                with pytest.raises(LockNotHeldError) as exc_info:
                    manager.refresh_lock(valid_lock_token)

                assert "Failed to read" in str(exc_info.value)

    def test_try_break_stale_lock_handles_oserror_on_stat(
        self,
        manager: ManifestManager,
    ) -> None:
        """Test _try_break_stale_lock handles OSError when getting stat."""
        dataset = "stat_error_test"
        lock_path = manager._lock_path(dataset)

        # Create lock file
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text('{"pid": 1234}')

        # Mock stat to raise OSError after the exists check
        original_stat = lock_path.stat
        call_count = [0]

        def mock_stat():
            call_count[0] += 1
            if call_count[0] > 0:
                raise OSError("Permission denied")
            return original_stat()

        with patch.object(type(lock_path), "stat", property(lambda self: mock_stat)):
            # Should not raise, just log debug
            manager._try_break_stale_lock(lock_path, dataset)

    def test_release_lock_handles_json_decode_error(
        self,
        manager: ManifestManager,
        temp_dirs: dict[str, Path],
    ) -> None:
        """Test _release_lock handles corrupt lock file gracefully."""
        lock_path = temp_dirs["locks"] / "corrupt_release.lock"

        # Create corrupt JSON
        with open(lock_path, "w") as f:
            f.write("not valid json{{{")

        lock_data = {"pid": os.getpid(), "hostname": "test", "writer_id": "test"}

        # Should not raise - handles JSONDecodeError gracefully
        manager._release_lock(lock_path, lock_data, "corrupt_release")

        # Lock file should still exist (we couldn't verify ownership)
        assert lock_path.exists()

    def test_try_break_stale_lock_with_live_process(
        self,
        manager: ManifestManager,
    ) -> None:
        """Test _try_break_stale_lock does NOT break lock when owner process is alive."""
        import socket
        import time

        dataset = "live_process_test"
        lock_path = manager._lock_path(dataset)
        current_hostname = socket.gethostname()
        current_pid = os.getpid()  # Our own PID - definitely alive!

        # Create lock file with current hostname and OUR PID (a live process)
        lock_data = {
            "pid": current_pid,
            "hostname": current_hostname,
            "writer_id": "live-writer",
            "acquired_at": datetime.now(UTC).isoformat(),
            "expires_at": (datetime.now(UTC) + timedelta(hours=4)).isoformat(),
        }
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "w") as f:
            json.dump(lock_data, f)

        # Set mtime to be older than soft timeout
        old_time = time.time() - 350  # 5+ minutes ago
        os.utime(lock_path, (old_time, old_time))

        # Should NOT break the lock since process is alive
        manager._try_break_stale_lock(lock_path, dataset)

        # Lock should still exist
        assert lock_path.exists()

    def test_save_manifest_with_expected_bytes(
        self,
        manager: ManifestManager,
        valid_lock_token: LockToken,
        tmp_path: Path,
    ) -> None:
        """Test save_manifest uses expected_bytes when provided."""
        dataset = "expected_bytes_test"
        valid_lock_token.lock_path = valid_lock_token.lock_path.parent / f"{dataset}.lock"
        with open(valid_lock_token.lock_path, "w") as f:
            json.dump(valid_lock_token.to_dict(), f)

        test_file = tmp_path / "test.parquet"
        test_file.write_text("content")

        manifest = SyncManifest(
            dataset=dataset,
            sync_timestamp=datetime.now(UTC),
            start_date=date(2024, 1, 1),
            end_date=date(2024, 1, 31),
            row_count=100,
            checksum="test_checksum",
            schema_version="v1.0.0",
            wrds_query_hash="test_hash",
            file_paths=[str(test_file)],
            validation_status="passed",
        )

        # Should succeed with explicit expected_bytes
        manager.save_manifest(manifest, valid_lock_token, expected_bytes=1000)

        loaded = manager.load_manifest(dataset)
        assert loaded is not None
        assert loaded.dataset == dataset
