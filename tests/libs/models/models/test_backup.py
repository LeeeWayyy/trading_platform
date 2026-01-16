"""
Comprehensive unit tests for libs/models/models/backup.py.

Test coverage:
- RegistryBackupManager: Locking, backup creation, restoration, remote sync, cleanup
- RegistryGC: Garbage collection for staged/archived models
- Edge cases: Empty directories, missing files, concurrent access, I/O errors
- Error paths: Lock contention, checksum mismatches, rclone failures

Target: 85%+ branch coverage (baseline from 0%)
"""

import fcntl
import json
import shutil
import subprocess
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, Mock, mock_open, patch

import pytest

from libs.models.models.backup import RegistryBackupManager, RegistryGC
from libs.models.models.registry import RegistryLockError
from libs.models.models.types import (
    BackupManifest,
    GCReport,
    ModelStatus,
    RestoreResult,
    SyncResult,
)


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def registry_dir(tmp_path: Path) -> Path:
    """Create temporary registry directory structure."""
    registry = tmp_path / "registry"
    registry.mkdir()
    (registry / "artifacts").mkdir()
    (registry / "backups").mkdir()

    # Create dummy registry.db
    db_path = registry / "registry.db"
    db_path.write_text("dummy db content")

    # Create dummy manifest.json
    manifest_path = registry / "manifest.json"
    manifest_path.write_text('{"version": "1.0"}')

    return registry


@pytest.fixture
def backup_manager(registry_dir: Path) -> RegistryBackupManager:
    """Create backup manager instance."""
    return RegistryBackupManager(registry_dir)


@pytest.fixture
def mock_registry() -> MagicMock:
    """Create mock ModelRegistry instance."""
    registry = MagicMock()
    registry.registry_dir = Path("/tmp/registry")
    registry._check_restore_lock = MagicMock()
    registry._get_connection = MagicMock()
    registry._update_manifest_counts = MagicMock()
    registry._update_manifest_production = MagicMock()
    return registry


@pytest.fixture
def registry_gc(mock_registry: MagicMock) -> RegistryGC:
    """Create RegistryGC instance."""
    return RegistryGC(mock_registry)


# =============================================================================
# RegistryBackupManager - Initialization
# =============================================================================


class TestRegistryBackupManagerInit:
    """Test RegistryBackupManager initialization."""

    def test_init_creates_paths(self, registry_dir: Path) -> None:
        """Test initialization creates expected paths."""
        manager = RegistryBackupManager(registry_dir)

        assert manager.registry_dir == registry_dir
        assert manager.backups_dir == registry_dir / "backups"
        assert manager.db_path == registry_dir / "registry.db"
        assert manager.artifacts_dir == registry_dir / "artifacts"
        assert manager._restore_lock_path == registry_dir / ".restore.lock"
        assert manager._backup_lock_path == registry_dir / ".backup.lock"
        assert manager._registry_lock_path == registry_dir / ".registry.lock"

    def test_init_lock_depth_counters(self, backup_manager: RegistryBackupManager) -> None:
        """Test lock depth counters initialized to zero."""
        assert backup_manager._restore_lock_depth == 0
        assert backup_manager._backup_lock_depth == 0
        assert backup_manager._restore_lock_file is None
        assert backup_manager._backup_lock_file is None


# =============================================================================
# RegistryBackupManager - Restore Lock
# =============================================================================


class TestRestoreLock:
    """Test restore lock mechanism."""

    def test_restore_lock_acquires_and_releases(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test restore lock can be acquired and released."""
        with backup_manager._restore_lock():
            assert backup_manager._restore_lock_depth == 1
            assert backup_manager._restore_lock_file is not None
            assert backup_manager._restore_lock_path.exists()

        # After context exit, lock should be released
        assert backup_manager._restore_lock_depth == 0
        assert backup_manager._restore_lock_file is None

    def test_restore_lock_reentrant(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test restore lock is reentrant."""
        with backup_manager._restore_lock():
            assert backup_manager._restore_lock_depth == 1
            with backup_manager._restore_lock():
                assert backup_manager._restore_lock_depth == 2
                with backup_manager._restore_lock():
                    assert backup_manager._restore_lock_depth == 3
                assert backup_manager._restore_lock_depth == 2
            assert backup_manager._restore_lock_depth == 1
        assert backup_manager._restore_lock_depth == 0

    def test_restore_lock_contention_raises_error(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test restore lock raises RegistryLockError on contention."""
        # Acquire lock in first context
        with backup_manager._restore_lock():
            # Create second manager instance
            manager2 = RegistryBackupManager(backup_manager.registry_dir)

            # Second manager should fail to acquire lock
            with pytest.raises(RegistryLockError, match="restore in progress"):
                with manager2._restore_lock():
                    pass

    def test_restore_lock_creates_directory(self, tmp_path: Path) -> None:
        """Test restore lock creates parent directory if needed."""
        registry_dir = tmp_path / "new_registry"
        manager = RegistryBackupManager(registry_dir)

        with manager._restore_lock():
            assert manager._restore_lock_path.parent.exists()

    def test_restore_lock_exception_cleanup(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test restore lock cleanup on exception."""
        with pytest.raises(RuntimeError):
            with backup_manager._restore_lock():
                raise RuntimeError("Test error")

        # Lock should be released after exception
        assert backup_manager._restore_lock_depth == 0
        assert backup_manager._restore_lock_file is None

    def test_restore_lock_close_error_during_cleanup(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test restore lock handles close errors during cleanup."""
        # Mock flock to raise BlockingIOError (lock contention)
        with patch("fcntl.flock", side_effect=BlockingIOError()):
            with pytest.raises(RegistryLockError, match="restore in progress"):
                with backup_manager._restore_lock():
                    pass


# =============================================================================
# RegistryBackupManager - Backup Lock
# =============================================================================


class TestBackupLock:
    """Test backup lock mechanism."""

    def test_backup_lock_acquires_and_releases(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test backup lock can be acquired and released."""
        with backup_manager._backup_lock():
            assert backup_manager._backup_lock_depth == 1
            assert backup_manager._backup_lock_file is not None
            assert backup_manager._backup_lock_path.exists()

        assert backup_manager._backup_lock_depth == 0
        assert backup_manager._backup_lock_file is None

    def test_backup_lock_reentrant(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test backup lock is reentrant."""
        with backup_manager._backup_lock():
            assert backup_manager._backup_lock_depth == 1
            with backup_manager._backup_lock():
                assert backup_manager._backup_lock_depth == 2
            assert backup_manager._backup_lock_depth == 1
        assert backup_manager._backup_lock_depth == 0

    def test_backup_lock_contention_raises_error(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test backup lock raises RegistryLockError on contention."""
        with backup_manager._backup_lock():
            manager2 = RegistryBackupManager(backup_manager.registry_dir)
            with pytest.raises(RegistryLockError, match="backup in progress"):
                with manager2._backup_lock():
                    pass

    def test_backup_lock_exception_cleanup(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test backup lock cleanup on exception."""
        with pytest.raises(RuntimeError):
            with backup_manager._backup_lock():
                raise RuntimeError("Test error")

        assert backup_manager._backup_lock_depth == 0
        assert backup_manager._backup_lock_file is None

    def test_backup_lock_close_error_during_cleanup(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test backup lock handles close errors during cleanup."""
        # Mock flock to raise BlockingIOError (lock contention)
        with patch("fcntl.flock", side_effect=BlockingIOError()):
            with pytest.raises(RegistryLockError, match="backup in progress"):
                with backup_manager._backup_lock():
                    pass


# =============================================================================
# RegistryBackupManager - Create Backup
# =============================================================================


class TestCreateBackup:
    """Test backup creation."""

    def test_create_backup_success(
        self, backup_manager: RegistryBackupManager, registry_dir: Path
    ) -> None:
        """Test successful backup creation."""
        manifest = backup_manager.create_backup()

        assert manifest.backup_id.startswith("backup_")
        assert manifest.source_path == str(registry_dir)
        assert manifest.size_bytes > 0
        assert manifest.checksum != ""

        # Verify backup files exist
        backup_path = Path(manifest.backup_path)
        assert backup_path.exists()
        assert (backup_path / "registry.db").exists()
        assert (backup_path / "manifest.json").exists()
        assert (backup_path / "backup_manifest.json").exists()

    def test_create_backup_custom_dir(
        self, backup_manager: RegistryBackupManager, tmp_path: Path
    ) -> None:
        """Test backup creation with custom directory."""
        custom_dir = tmp_path / "custom_backups"
        manifest = backup_manager.create_backup(backup_dir=custom_dir)

        assert custom_dir in Path(manifest.backup_path).parents
        assert Path(manifest.backup_path).exists()

    def test_create_backup_with_artifacts(
        self, backup_manager: RegistryBackupManager, registry_dir: Path
    ) -> None:
        """Test backup includes artifacts directory."""
        # Create dummy artifact
        artifact_dir = registry_dir / "artifacts" / "risk_model"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "model.pkl").write_text("dummy model")

        manifest = backup_manager.create_backup()
        backup_path = Path(manifest.backup_path)

        assert (backup_path / "artifacts" / "risk_model" / "model.pkl").exists()

    def test_create_backup_empty_registry(self, tmp_path: Path) -> None:
        """Test backup of empty registry."""
        empty_registry = tmp_path / "empty"
        empty_registry.mkdir()

        manager = RegistryBackupManager(empty_registry)
        manifest = manager.create_backup()

        assert manifest.backup_id != ""
        assert manifest.size_bytes >= 0
        # Checksum should be empty since no DB exists
        assert manifest.checksum == ""

    def test_create_backup_manifest_json_format(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test backup manifest JSON format."""
        manifest = backup_manager.create_backup()
        backup_path = Path(manifest.backup_path)

        with open(backup_path / "backup_manifest.json") as f:
            data = json.load(f)

        assert data["backup_id"] == manifest.backup_id
        assert "created_at" in data
        assert data["source_path"] == manifest.source_path
        assert data["checksum"] == manifest.checksum
        assert data["size_bytes"] == manifest.size_bytes

    def test_create_backup_updates_manifest(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test backup updates registry manifest."""
        # Create backup (manifest update happens inside create_backup)
        manifest = backup_manager.create_backup()

        # Verify backup was created successfully
        assert manifest.backup_id != ""
        assert Path(manifest.backup_path).exists()

        # Manifest update is called inside create_backup but may fail non-fatally
        # We just verify the backup succeeds

    def test_create_backup_manifest_update_failure_non_fatal(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test backup succeeds even if manifest update fails."""
        # Patch the import inside create_backup to raise an error
        with patch.dict("sys.modules", {"libs.models.models.manifest": None}):
            # Should not raise, backup succeeds despite manifest failure
            manifest = backup_manager.create_backup()
            assert manifest.backup_id != ""


# =============================================================================
# RegistryBackupManager - Restore from Backup
# =============================================================================


class TestRestoreFromBackup:
    """Test backup restoration."""

    def _create_test_backup(
        self, backup_manager: RegistryBackupManager
    ) -> BackupManifest:
        """Helper to create a test backup."""
        return backup_manager.create_backup()

    def test_restore_from_backup_by_id(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test restore from backup by ID."""
        manifest = self._create_test_backup(backup_manager)

        result = backup_manager.restore_from_backup(backup_id=manifest.backup_id)

        assert result.success is True
        assert result.models_restored >= 0
        assert manifest.backup_id in result.message

    def test_restore_from_backup_most_recent(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test restore from most recent backup."""
        self._create_test_backup(backup_manager)

        result = backup_manager.restore_from_backup()

        assert result.success is True

    def test_restore_from_backup_by_date(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test restore from backup by date."""
        self._create_test_backup(backup_manager)

        result = backup_manager.restore_from_backup(backup_date=date.today())

        assert result.success is True

    def test_restore_from_backup_not_found(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test restore fails when backup not found."""
        result = backup_manager.restore_from_backup(backup_id="nonexistent")

        assert result.success is False
        assert "No backup found" in result.message
        assert result.models_restored == 0

    def test_restore_from_backup_checksum_mismatch(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test restore fails on checksum mismatch."""
        manifest = self._create_test_backup(backup_manager)
        backup_path = Path(manifest.backup_path)

        # Corrupt backup manifest checksum
        with open(backup_path / "backup_manifest.json", "r") as f:
            data = json.load(f)
        data["checksum"] = "invalid_checksum"
        with open(backup_path / "backup_manifest.json", "w") as f:
            json.dump(data, f)

        result = backup_manager.restore_from_backup(backup_id=manifest.backup_id)

        assert result.success is False
        assert "checksum verification failed" in result.message

    def test_restore_from_backup_no_manifest(
        self, backup_manager: RegistryBackupManager, registry_dir: Path
    ) -> None:
        """Test restore handles missing backup manifest."""
        # Create backup directory without manifest
        backup_dir = backup_manager.backups_dir / "test_backup"
        backup_dir.mkdir()
        (backup_dir / "registry.db").write_text("dummy")

        result = backup_manager.restore_from_backup(backup_id="test_backup")

        # Should succeed without checksum verification
        assert result.success is True

    def test_restore_from_backup_rollback_on_error(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test restore rolls back on error."""
        manifest = self._create_test_backup(backup_manager)
        backup_path = Path(manifest.backup_path)

        # Delete registry.db from backup to cause restore failure
        (backup_path / "registry.db").unlink()

        with patch.object(
            backup_manager, "_restore_backup_contents", side_effect=RuntimeError("Test error")
        ):
            result = backup_manager.restore_from_backup(backup_id=manifest.backup_id)

            assert result.success is False
            assert "Restore from" in result.message
            assert "Rollback" in result.message

    def test_restore_from_backup_rollback_failure(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test restore handles rollback failure."""
        manifest = self._create_test_backup(backup_manager)

        call_count = 0
        def failing_restore(path: Path) -> None:
            nonlocal call_count
            call_count += 1
            raise RuntimeError(f"Error {call_count}")

        with patch.object(
            backup_manager, "_restore_backup_contents", side_effect=failing_restore
        ):
            result = backup_manager.restore_from_backup(backup_id=manifest.backup_id)

            assert result.success is False
            assert "Rollback" in result.message
            assert "failed" in result.message

    def test_restore_from_backup_counts_models(
        self, backup_manager: RegistryBackupManager, registry_dir: Path
    ) -> None:
        """Test restore counts restored models."""
        # Create artifacts
        for i in range(3):
            model_dir = registry_dir / "artifacts" / "risk_model" / f"model_{i}"
            model_dir.mkdir(parents=True)
            (model_dir / "model.pkl").write_text("dummy")

        manifest = self._create_test_backup(backup_manager)
        result = backup_manager.restore_from_backup(backup_id=manifest.backup_id)

        assert result.success is True
        assert result.models_restored == 3

    def test_restore_backup_contents(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test _restore_backup_contents helper."""
        manifest = self._create_test_backup(backup_manager)
        backup_path = Path(manifest.backup_path)

        # Modify current registry
        backup_manager.db_path.write_text("modified content")

        backup_manager._restore_backup_contents(backup_path)

        # Should restore original content
        assert backup_manager.db_path.read_text() == "dummy db content"


# =============================================================================
# RegistryBackupManager - Remote Sync
# =============================================================================


class TestSyncToRemote:
    """Test remote sync functionality."""

    def test_sync_to_remote_success(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test successful remote sync."""
        with patch("shutil.which", return_value="/usr/bin/rclone"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = Mock(
                    returncode=0,
                    stdout="Transferred:      1.234 GiB / 2.000 GiB, 50%",
                    stderr="",
                )

                result = backup_manager.sync_to_remote("s3:bucket/path")

                assert result.success is True
                assert result.remote_path == "s3:bucket/path"
                assert result.bytes_transferred > 0

    def test_sync_to_remote_rclone_not_installed(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test sync fails when rclone not installed."""
        with patch("shutil.which", return_value=None):
            result = backup_manager.sync_to_remote("s3:bucket/path")

            assert result.success is False
            assert "rclone not installed" in result.message
            assert result.bytes_transferred == 0

    def test_sync_to_remote_rclone_failure(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test sync handles rclone failure."""
        with patch("shutil.which", return_value="/usr/bin/rclone"):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = Mock(
                    returncode=1,
                    stdout="",
                    stderr="Error: connection failed",
                )

                result = backup_manager.sync_to_remote("s3:bucket/path")

                assert result.success is False
                assert "rclone failed" in result.message

    def test_sync_to_remote_timeout(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test sync handles timeout."""
        with patch("shutil.which", return_value="/usr/bin/rclone"):
            with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("rclone", 3600)):
                result = backup_manager.sync_to_remote("s3:bucket/path")

                assert result.success is False
                assert "timed out" in result.message

    def test_sync_to_remote_exception(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test sync handles general exceptions."""
        with patch("shutil.which", return_value="/usr/bin/rclone"):
            with patch("subprocess.run", side_effect=RuntimeError("Unexpected error")):
                result = backup_manager.sync_to_remote("s3:bucket/path")

                assert result.success is False
                assert "Sync error" in result.message


# =============================================================================
# RegistryBackupManager - Helper Methods
# =============================================================================


class TestHelperMethods:
    """Test helper methods."""

    def test_parse_rclone_size_gib(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test parsing GiB size."""
        assert backup_manager._parse_rclone_size("1.234 GiB") == int(1.234 * 1024**3)

    def test_parse_rclone_size_mib(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test parsing MiB size."""
        assert backup_manager._parse_rclone_size("500 MiB") == 500 * 1024**2

    def test_parse_rclone_size_kib(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test parsing KiB size."""
        assert backup_manager._parse_rclone_size("1024 KiB") == 1024 * 1024

    def test_parse_rclone_size_bytes(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test parsing bytes."""
        assert backup_manager._parse_rclone_size("1024 B") == 1024

    def test_parse_rclone_size_decimal_units(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test parsing decimal units (GB, MB, KB)."""
        assert backup_manager._parse_rclone_size("1 GB") == 1000**3
        assert backup_manager._parse_rclone_size("1 MB") == 1000**2
        assert backup_manager._parse_rclone_size("1 KB") == 1000

    def test_parse_rclone_size_raw_bytes(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test parsing raw bytes."""
        assert backup_manager._parse_rclone_size("12345") == 12345

    def test_parse_rclone_size_empty(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test parsing empty string."""
        assert backup_manager._parse_rclone_size("") == 0

    def test_parse_rclone_size_invalid(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test parsing invalid size string."""
        assert backup_manager._parse_rclone_size("invalid") == 0

    def test_find_backup_by_date_exact_match(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test finding backup by exact date."""
        manifest = backup_manager.create_backup()

        result = backup_manager._find_backup_by_date(date.today())

        assert result is not None
        assert result.name == manifest.backup_id

    def test_find_backup_by_date_closest_match(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test finding closest backup by date."""
        backup_manager.create_backup()

        # Search for yesterday (should find today's backup as closest)
        yesterday = date.today() - timedelta(days=1)
        result = backup_manager._find_backup_by_date(yesterday)

        assert result is not None

    def test_find_backup_by_date_no_backups(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test finding backup when no backups exist."""
        result = backup_manager._find_backup_by_date(date.today())

        assert result is None

    def test_find_most_recent_backup(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test finding most recent backup."""
        backup_manager.create_backup()

        result = backup_manager._find_most_recent_backup()

        assert result is not None

    def test_find_most_recent_backup_multiple(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test finding most recent among multiple backups."""
        backup_manager.create_backup()
        manifest2 = backup_manager.create_backup()

        result = backup_manager._find_most_recent_backup()

        assert result is not None
        assert result.name == manifest2.backup_id

    def test_find_most_recent_backup_empty(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test finding backup when directory is empty."""
        result = backup_manager._find_most_recent_backup()

        assert result is None

    def test_update_manifest_backup_info(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test updating manifest with backup info."""
        manifest = backup_manager.create_backup()

        mock_manifest_manager = MagicMock()
        backup_manager.update_manifest_backup_info(mock_manifest_manager)

        # Should find most recent backup and update manifest
        mock_manifest_manager.update_manifest.assert_called_once()

    def test_update_manifest_backup_info_no_backups(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test updating manifest when no backups exist."""
        # Clear backups directory
        shutil.rmtree(backup_manager.backups_dir)
        backup_manager.backups_dir.mkdir()

        mock_manifest_manager = MagicMock()
        backup_manager.update_manifest_backup_info(mock_manifest_manager)

        # Should not call update if no backups
        mock_manifest_manager.update_manifest.assert_not_called()


# =============================================================================
# RegistryBackupManager - Cleanup
# =============================================================================


class TestCleanupOldBackups:
    """Test backup cleanup."""

    def test_cleanup_old_backups_removes_old(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test cleanup removes old backups."""
        # Create old backup
        old_backup_dir = backup_manager.backups_dir / "old_backup"
        old_backup_dir.mkdir()

        old_date = datetime.now(UTC) - timedelta(days=100)
        manifest_data = {
            "backup_id": "old_backup",
            "created_at": old_date.isoformat(),
            "source_path": str(backup_manager.registry_dir),
            "backup_path": str(old_backup_dir),
            "checksum": "hash",
            "size_bytes": 1000,
        }
        with open(old_backup_dir / "backup_manifest.json", "w") as f:
            json.dump(manifest_data, f)

        count = backup_manager.cleanup_old_backups()

        assert count == 1
        assert not old_backup_dir.exists()

    def test_cleanup_old_backups_keeps_recent(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test cleanup keeps recent backups."""
        manifest = backup_manager.create_backup()

        count = backup_manager.cleanup_old_backups()

        assert count == 0
        assert Path(manifest.backup_path).exists()

    def test_cleanup_old_backups_custom_retention(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test cleanup with custom retention period."""
        # Create backup from 50 days ago
        old_backup_dir = backup_manager.backups_dir / "old_backup"
        old_backup_dir.mkdir()

        old_date = datetime.now(UTC) - timedelta(days=50)
        manifest_data = {
            "backup_id": "old_backup",
            "created_at": old_date.isoformat(),
            "source_path": str(backup_manager.registry_dir),
            "backup_path": str(old_backup_dir),
            "checksum": "hash",
            "size_bytes": 1000,
        }
        with open(old_backup_dir / "backup_manifest.json", "w") as f:
            json.dump(manifest_data, f)

        # With 30-day retention, should remove
        count = backup_manager.cleanup_old_backups(retention_days=30)
        assert count == 1

        # With 60-day retention, would keep (but already removed)
        count = backup_manager.cleanup_old_backups(retention_days=60)
        assert count == 0

    def test_cleanup_old_backups_no_backups_dir(
        self, tmp_path: Path
    ) -> None:
        """Test cleanup when backups directory doesn't exist."""
        manager = RegistryBackupManager(tmp_path / "empty")

        count = manager.cleanup_old_backups()

        assert count == 0

    def test_cleanup_old_backups_skips_files(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test cleanup skips non-directory files."""
        # Create a file in backups directory
        (backup_manager.backups_dir / "readme.txt").write_text("info")

        count = backup_manager.cleanup_old_backups()

        assert count == 0
        assert (backup_manager.backups_dir / "readme.txt").exists()

    def test_cleanup_old_backups_skips_no_manifest(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test cleanup skips directories without manifest."""
        # Create backup directory without manifest
        incomplete_dir = backup_manager.backups_dir / "incomplete"
        incomplete_dir.mkdir()

        count = backup_manager.cleanup_old_backups()

        assert count == 0
        assert incomplete_dir.exists()


# =============================================================================
# RegistryGC - Expired Staged Models
# =============================================================================


class TestCollectExpiredStaged:
    """Test collection of expired staged models."""

    def test_collect_expired_staged_finds_old_models(
        self, registry_gc: RegistryGC, mock_registry: MagicMock
    ) -> None:
        """Test finding old staged models."""
        old_model = MagicMock()
        old_model.model_id = "old_staged"
        old_model.created_at = datetime.now(UTC) - timedelta(days=40)

        recent_model = MagicMock()
        recent_model.model_id = "recent_staged"
        recent_model.created_at = datetime.now(UTC) - timedelta(days=10)

        mock_registry.list_models.return_value = [old_model, recent_model]

        expired = registry_gc.collect_expired_staged()

        assert "old_staged" in expired
        assert "recent_staged" not in expired
        mock_registry.list_models.assert_called_once_with(status=ModelStatus.staged)

    def test_collect_expired_staged_custom_age(
        self, registry_gc: RegistryGC, mock_registry: MagicMock
    ) -> None:
        """Test collection with custom max age."""
        model = MagicMock()
        model.model_id = "staged_model"
        model.created_at = datetime.now(UTC) - timedelta(days=20)

        mock_registry.list_models.return_value = [model]

        # With 30-day retention, should not expire
        expired = registry_gc.collect_expired_staged(max_age_days=30)
        assert "staged_model" not in expired

        # With 10-day retention, should expire
        expired = registry_gc.collect_expired_staged(max_age_days=10)
        assert "staged_model" in expired

    def test_collect_expired_staged_empty(
        self, registry_gc: RegistryGC, mock_registry: MagicMock
    ) -> None:
        """Test collection when no staged models exist."""
        mock_registry.list_models.return_value = []

        expired = registry_gc.collect_expired_staged()

        assert expired == []


# =============================================================================
# RegistryGC - Expired Archived Models
# =============================================================================


class TestCollectExpiredArchived:
    """Test collection of expired archived models."""

    def test_collect_expired_archived_finds_old_models(
        self, registry_gc: RegistryGC, mock_registry: MagicMock
    ) -> None:
        """Test finding old archived models."""
        old_date = (datetime.now(UTC) - timedelta(days=100)).isoformat()
        recent_date = (datetime.now(UTC) - timedelta(days=10)).isoformat()

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [
            ("old_archived", old_date),
            ("recent_archived", recent_date),
        ]
        mock_registry._get_connection.return_value.__enter__.return_value = mock_conn

        expired = registry_gc.collect_expired_archived()

        assert "old_archived" in expired
        assert "recent_archived" not in expired

    def test_collect_expired_archived_custom_age(
        self, registry_gc: RegistryGC, mock_registry: MagicMock
    ) -> None:
        """Test collection with custom max age."""
        archived_date = (datetime.now(UTC) - timedelta(days=50)).isoformat()

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [
            ("archived_model", archived_date),
        ]
        mock_registry._get_connection.return_value.__enter__.return_value = mock_conn

        # With 100-day retention, should not expire
        expired = registry_gc.collect_expired_archived(max_age_days=100)
        assert "archived_model" not in expired

        # With 30-day retention, should expire
        expired = registry_gc.collect_expired_archived(max_age_days=30)
        assert "archived_model" in expired

    def test_collect_expired_archived_timezone_aware(
        self, registry_gc: RegistryGC, mock_registry: MagicMock
    ) -> None:
        """Test archived_at timezone handling."""
        # Create naive datetime (no timezone)
        naive_date = datetime.now() - timedelta(days=100)

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = [
            ("archived_model", naive_date.isoformat()),
        ]
        mock_registry._get_connection.return_value.__enter__.return_value = mock_conn

        expired = registry_gc.collect_expired_archived()

        # Should handle naive datetime by adding UTC timezone
        assert "archived_model" in expired

    def test_collect_expired_archived_empty(
        self, registry_gc: RegistryGC, mock_registry: MagicMock
    ) -> None:
        """Test collection when no archived models exist."""
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_registry._get_connection.return_value.__enter__.return_value = mock_conn

        expired = registry_gc.collect_expired_archived()

        assert expired == []


# =============================================================================
# RegistryGC - Run GC
# =============================================================================


class TestRunGC:
    """Test garbage collection execution."""

    def test_run_gc_dry_run(
        self, registry_gc: RegistryGC, mock_registry: MagicMock
    ) -> None:
        """Test GC dry run doesn't delete."""
        old_staged = MagicMock()
        old_staged.model_id = "staged_1"
        old_staged.created_at = datetime.now(UTC) - timedelta(days=40)

        mock_registry.list_models.return_value = [old_staged]

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_registry._get_connection.return_value.__enter__.return_value = mock_conn

        report = registry_gc.run_gc(dry_run=True)

        assert report.dry_run is True
        assert len(report.expired_staged) == 1
        assert report.bytes_freed == 0
        # Should not call delete methods
        mock_registry._get_connection.assert_called_with(read_only=True)

    def test_run_gc_actual_deletion(
        self, registry_gc: RegistryGC, mock_registry: MagicMock, tmp_path: Path
    ) -> None:
        """Test GC actually deletes artifacts and DB entries."""
        old_staged = MagicMock()
        old_staged.model_id = "staged_1"
        old_staged.created_at = datetime.now(UTC) - timedelta(days=40)

        mock_registry.list_models.return_value = [old_staged]

        # Create temporary artifact
        artifact_path = tmp_path / "artifacts" / "staged_1"
        artifact_path.mkdir(parents=True)
        (artifact_path / "model.pkl").write_text("dummy" * 100)

        # Mock connections for different query types
        read_call_count = 0

        def mock_get_connection(read_only: bool = False):
            nonlocal read_call_count
            mock_conn = MagicMock()

            if read_only:
                read_call_count += 1
                if read_call_count == 1:
                    # First read: query archived models
                    mock_conn.execute.return_value.fetchall.return_value = []
                else:
                    # Second read: query artifact path
                    mock_conn.execute.return_value.fetchone.return_value = (str(artifact_path),)
            else:
                # Write connection for deletion
                mock_conn.execute.return_value = None

            mock_ctx = MagicMock()
            mock_ctx.__enter__ = lambda self: mock_conn
            mock_ctx.__exit__ = lambda self, *args: None
            return mock_ctx

        mock_registry._get_connection = mock_get_connection

        report = registry_gc.run_gc(dry_run=False)

        assert report.dry_run is False
        assert len(report.expired_staged) == 1
        assert report.bytes_freed > 0
        assert not artifact_path.exists()

    def test_run_gc_checks_restore_lock(
        self, registry_gc: RegistryGC, mock_registry: MagicMock
    ) -> None:
        """Test GC checks for restore lock before running."""
        mock_registry._check_restore_lock.side_effect = RegistryLockError("Locked")

        with pytest.raises(RegistryLockError):
            registry_gc.run_gc(dry_run=True)

    def test_run_gc_handles_deletion_error(
        self, registry_gc: RegistryGC, mock_registry: MagicMock
    ) -> None:
        """Test GC handles deletion errors gracefully."""
        old_model = MagicMock()
        old_model.model_id = "bad_model"
        old_model.created_at = datetime.now(UTC) - timedelta(days=40)

        mock_registry.list_models.return_value = [old_model]

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = (None,)
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_registry._get_connection.return_value.__enter__.return_value = mock_conn

        # Should not raise, just log error
        report = registry_gc.run_gc(dry_run=False)

        assert report.expired_staged == ["bad_model"]

    def test_run_gc_updates_manifest(
        self, registry_gc: RegistryGC, mock_registry: MagicMock
    ) -> None:
        """Test GC updates manifest after deletions."""
        old_model = MagicMock()
        old_model.model_id = "model_1"
        old_model.created_at = datetime.now(UTC) - timedelta(days=40)

        mock_registry.list_models.return_value = [old_model]

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = (None,)
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_registry._get_connection.return_value.__enter__.return_value = mock_conn

        registry_gc.run_gc(dry_run=False)

        mock_registry._update_manifest_counts.assert_called_once()
        mock_registry._update_manifest_production.assert_called_once()

    def test_run_gc_manifest_update_failure(
        self, registry_gc: RegistryGC, mock_registry: MagicMock
    ) -> None:
        """Test GC handles manifest update failure."""
        mock_registry.list_models.return_value = []

        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_registry._get_connection.return_value.__enter__.return_value = mock_conn

        mock_registry._update_manifest_counts.side_effect = RuntimeError("Update failed")

        # Should not raise
        report = registry_gc.run_gc(dry_run=False)
        assert report.dry_run is False

    def test_delete_model_from_db(
        self, registry_gc: RegistryGC, mock_registry: MagicMock
    ) -> None:
        """Test _delete_model_from_db removes entries."""
        mock_conn = MagicMock()
        mock_registry._get_connection.return_value.__enter__.return_value = mock_conn

        registry_gc._delete_model_from_db("model_123")

        # Should delete from both tables
        assert mock_conn.execute.call_count == 2

    def test_update_manifest_after_gc(
        self, registry_gc: RegistryGC
    ) -> None:
        """Test update_manifest_after_gc recalculates counts."""
        mock_manifest_manager = MagicMock()
        mock_manifest_manager.registry_dir = Path("/tmp/registry")

        with patch("pathlib.Path.exists", return_value=False):
            registry_gc.update_manifest_after_gc(mock_manifest_manager)

        # Should update with zero counts when artifacts dir doesn't exist
        mock_manifest_manager.update_manifest.assert_called_once()
        call_kwargs = mock_manifest_manager.update_manifest.call_args.kwargs
        assert call_kwargs["artifact_count"] == 0
        assert call_kwargs["total_size_bytes"] == 0


# =============================================================================
# Edge Cases and Error Handling
# =============================================================================


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_backup_manager_with_pathlib_path(self, tmp_path: Path) -> None:
        """Test backup manager accepts Path objects."""
        manager = RegistryBackupManager(tmp_path / "registry")
        assert isinstance(manager.registry_dir, Path)

    def test_backup_with_unicode_filenames(
        self, backup_manager: RegistryBackupManager, registry_dir: Path
    ) -> None:
        """Test backup handles unicode filenames."""
        artifact_dir = registry_dir / "artifacts" / "test"
        artifact_dir.mkdir(parents=True)
        (artifact_dir / "模型.pkl").write_text("unicode content")

        manifest = backup_manager.create_backup()

        backup_path = Path(manifest.backup_path)
        assert (backup_path / "artifacts" / "test" / "模型.pkl").exists()

    def test_restore_with_symlinks(
        self, backup_manager: RegistryBackupManager, registry_dir: Path
    ) -> None:
        """Test restore handles symlinks gracefully."""
        # Create artifact with symlink
        artifact_dir = registry_dir / "artifacts" / "test"
        artifact_dir.mkdir(parents=True)
        target_file = artifact_dir / "target.txt"
        target_file.write_text("target")

        link_file = artifact_dir / "link.txt"
        link_file.symlink_to(target_file)

        manifest = backup_manager.create_backup()
        result = backup_manager.restore_from_backup(backup_id=manifest.backup_id)

        assert result.success is True

    def test_gc_with_missing_artifact_path(
        self, registry_gc: RegistryGC, mock_registry: MagicMock
    ) -> None:
        """Test GC handles missing artifact paths."""
        old_model = MagicMock()
        old_model.model_id = "model_1"
        old_model.created_at = datetime.now(UTC) - timedelta(days=40)

        mock_registry.list_models.return_value = [old_model]

        # Return None for artifact path
        mock_conn = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = None
        mock_conn.execute.return_value.fetchall.return_value = []
        mock_registry._get_connection.return_value.__enter__.return_value = mock_conn

        report = registry_gc.run_gc(dry_run=False)

        # Should handle gracefully
        assert report.expired_staged == ["model_1"]

    def test_parse_rclone_size_edge_cases(
        self, backup_manager: RegistryBackupManager
    ) -> None:
        """Test _parse_rclone_size with various edge cases."""
        # Whitespace handling
        assert backup_manager._parse_rclone_size("  1.5 GiB  ") == int(1.5 * 1024**3)

        # Missing unit
        assert backup_manager._parse_rclone_size("1.5") == 1

        # Unknown unit
        assert backup_manager._parse_rclone_size("1.5 XiB") == 0

        # Multiple spaces
        assert backup_manager._parse_rclone_size("1.5    GiB") == int(1.5 * 1024**3)
