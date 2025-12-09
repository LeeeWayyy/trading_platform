"""
Model Registry backup and retention management.

This module provides:
- RegistryBackupManager: Create and restore backups
- RegistryGC: Garbage collection for expired models
- Retention policy enforcement

Backup Policy (per spec ~2046-2050):
- Primary: Local filesystem (data/models/)
- Backup: Optional S3/GCS sync via rclone
- Backup frequency: Daily at 02:00 UTC
- Retention: 90 days for backups

Retention Policy (per spec ~2121-2127):
- Production models: retained indefinitely
- Staged models: 30 days after promotion or rejection
- Archived models: 90 days after archival
- Artifacts: checksum re-validated on every load
- GC job: weekly cleanup of expired artifacts
"""

from __future__ import annotations

import fcntl
import json
import logging
import shutil
import subprocess
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from libs.models.registry import RegistryLockError
from libs.models.serialization import compute_checksum
from libs.models.types import BackupManifest, GCReport, RestoreResult, SyncResult

if TYPE_CHECKING:
    from libs.models.manifest import RegistryManifestManager
    from libs.models.registry import ModelRegistry

logger = logging.getLogger(__name__)


# =============================================================================
# Backup Manager
# =============================================================================


class RegistryBackupManager:
    """Manage registry backups with retention.

    Features:
    - Local filesystem backups
    - Optional remote sync via rclone
    - Checksum verification
    - Retention policy (90 days default)

    Example:
        manager = RegistryBackupManager(registry_dir=Path("data/models"))

        # Create backup
        manifest = manager.create_backup(backup_dir=Path("data/models/backups"))

        # Restore from backup
        result = manager.restore_from_backup(date(2024, 1, 15))

        # Sync to remote
        result = manager.sync_to_remote("s3:my-bucket/model-backups/")
    """

    BACKUP_RETENTION_DAYS = 90

    def __init__(self, registry_dir: Path) -> None:
        """Initialize backup manager.

        Args:
            registry_dir: Path to registry directory.
        """
        self.registry_dir = Path(registry_dir)
        self.backups_dir = self.registry_dir / "backups"
        self.db_path = self.registry_dir / "registry.db"
        self.artifacts_dir = self.registry_dir / "artifacts"
        self._restore_lock_path = self.registry_dir / ".restore.lock"
        self._backup_lock_path = self.registry_dir / ".backup.lock"
        # Track lock depth for reentrant locking (nested calls don't delete file early)
        self._restore_lock_depth = 0
        self._restore_lock_file: Any = None
        self._backup_lock_depth = 0
        self._backup_lock_file: Any = None

    @contextmanager
    def _restore_lock(self) -> Iterator[None]:
        """Acquire exclusive lock during restore to prevent serving half-restored state.

        This prevents live readers from accessing the registry while restore is in progress.
        Readers should check for the lock file before reading critical data.

        This lock is reentrant - nested calls increment depth counter and only the
        outermost call releases the lock and deletes the file.
        """
        # Reentrant: if already locked, just increment depth
        if self._restore_lock_depth > 0:
            self._restore_lock_depth += 1
            logger.debug(f"Restore lock reentrant call, depth={self._restore_lock_depth}")
            try:
                yield
            finally:
                self._restore_lock_depth -= 1
            return

        # First acquisition: create lock file and acquire flock
        self._restore_lock_path.parent.mkdir(parents=True, exist_ok=True)
        # Use a+ to avoid truncating file if another process holds lock
        self._restore_lock_file = open(self._restore_lock_path, "a+")
        try:
            logger.info("Acquiring restore lock")
            # Use LOCK_NB to fail fast if locked. Waiting is dangerous because the
            # previous owner will unlink the file, and we would lock a deleted file.
            fcntl.flock(self._restore_lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            # Truncate and write info after acquiring lock
            self._restore_lock_file.seek(0)
            self._restore_lock_file.truncate()
            self._restore_lock_file.write(f"Restore in progress since {datetime.now(UTC).isoformat()}\n")
            self._restore_lock_file.flush()

            self._restore_lock_depth = 1
            yield
        except (BlockingIOError, OSError):
            # Lock held by another process
            if self._restore_lock_file:
                self._restore_lock_file.close()
                self._restore_lock_file = None
            # Check if it was a locking error or actual lock held
            raise RegistryLockError("Registry restore in progress (lock held).") from None
        except Exception:
            # Cleanup on other errors
            if self._restore_lock_file:
                self._restore_lock_file.close()
                self._restore_lock_file = None
            raise
        finally:
            if self._restore_lock_depth == 1: # Only releasing if we acquired it
                self._restore_lock_depth = 0
                if self._restore_lock_file:
                    try:
                        fcntl.flock(self._restore_lock_file.fileno(), fcntl.LOCK_UN)
                        self._restore_lock_file.close()
                    except Exception as e:
                        logger.error(f"Error releasing restore lock: {e}")
                    self._restore_lock_file = None
                    # Remove lock file after successful release
                    try:
                        self._restore_lock_path.unlink()
                    except FileNotFoundError:
                        pass
                    logger.info("Released restore lock")
            elif self._restore_lock_depth > 1:
                # Reentrant release - just decrement
                self._restore_lock_depth -= 1

    @contextmanager
    def _backup_lock(self) -> Iterator[None]:
        """Acquire lock during backup to prevent writes.

        Unlike restore lock, backup lock only blocks writes, not reads.
        This allows the registry to serve requests during backup while
        ensuring consistency in the backup snapshot.

        The lock is reentrant - nested calls increment depth counter.
        """
        # Reentrant: if already locked, just increment depth
        if self._backup_lock_depth > 0:
            self._backup_lock_depth += 1
            logger.debug(f"Backup lock reentrant call, depth={self._backup_lock_depth}")
            try:
                yield
            finally:
                self._backup_lock_depth -= 1
            return

        # First acquisition: create lock file and acquire flock
        self._backup_lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._backup_lock_file = open(self._backup_lock_path, "a+")
        try:
            logger.info("Acquiring backup lock")
            fcntl.flock(self._backup_lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

            # Truncate and write info after acquiring lock
            self._backup_lock_file.seek(0)
            self._backup_lock_file.truncate()
            self._backup_lock_file.write(f"Backup in progress since {datetime.now(UTC).isoformat()}\n")
            self._backup_lock_file.flush()

            self._backup_lock_depth = 1
            yield
        except (BlockingIOError, OSError):
            if self._backup_lock_file:
                self._backup_lock_file.close()
                self._backup_lock_file = None
            raise RegistryLockError("Registry backup in progress (lock held).") from None
        except Exception:
            if self._backup_lock_file:
                self._backup_lock_file.close()
                self._backup_lock_file = None
            raise
        finally:
            if self._backup_lock_depth == 1:
                self._backup_lock_depth = 0
                if self._backup_lock_file:
                    try:
                        fcntl.flock(self._backup_lock_file.fileno(), fcntl.LOCK_UN)
                        self._backup_lock_file.close()
                    except Exception as e:
                        logger.error(f"Error releasing backup lock: {e}")
                    self._backup_lock_file = None
                    try:
                        self._backup_lock_path.unlink()
                    except FileNotFoundError:
                        pass
                    logger.info("Released backup lock")
            elif self._backup_lock_depth > 1:
                self._backup_lock_depth -= 1

    def create_backup(self, backup_dir: Path | None = None) -> BackupManifest:
        """Create a full registry backup.

        Uses exclusive lock to prevent concurrent writes during backup,
        ensuring consistency between DB and artifact snapshots.

        Args:
            backup_dir: Override backup directory (default: registry_dir/backups).

        Returns:
            BackupManifest with backup details.
        """
        backup_dir = backup_dir or self.backups_dir
        backup_dir.mkdir(parents=True, exist_ok=True)

        # Generate backup ID
        now = datetime.now(UTC)
        backup_id = f"backup_{now.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
        backup_path = backup_dir / backup_id

        logger.info(f"Creating backup: {backup_id}")

        # Acquire backup lock to freeze registry writes during backup
        # Uses separate lock from restore - reads can continue during backup
        # Only writes are blocked to ensure consistency in the snapshot
        with self._backup_lock():
            # Create backup directory
            backup_path.mkdir(parents=True, exist_ok=True)

            # Copy database
            if self.db_path.exists():
                shutil.copy2(self.db_path, backup_path / "registry.db")

            # Copy manifest
            manifest_path = self.registry_dir / "manifest.json"
            if manifest_path.exists():
                shutil.copy2(manifest_path, backup_path / "manifest.json")

            # Copy artifacts directory
            if self.artifacts_dir.exists():
                shutil.copytree(
                    self.artifacts_dir,
                    backup_path / "artifacts",
                    dirs_exist_ok=True,
                )

        # Compute checksum of backup
        total_size = 0
        for file_path in backup_path.rglob("*"):
            if file_path.is_file():
                total_size += file_path.stat().st_size

        db_checksum = ""
        if (backup_path / "registry.db").exists():
            db_checksum = compute_checksum(backup_path / "registry.db")

        # Create backup manifest
        backup_manifest = BackupManifest(
            backup_id=backup_id,
            created_at=now,
            source_path=str(self.registry_dir),
            backup_path=str(backup_path),
            checksum=db_checksum,
            size_bytes=total_size,
        )

        # Write backup manifest
        manifest_data = {
            "backup_id": backup_manifest.backup_id,
            "created_at": backup_manifest.created_at.isoformat(),
            "source_path": backup_manifest.source_path,
            "backup_path": backup_manifest.backup_path,
            "checksum": backup_manifest.checksum,
            "size_bytes": backup_manifest.size_bytes,
        }
        with open(backup_path / "backup_manifest.json", "w") as f:
            json.dump(manifest_data, f, indent=2)

        logger.info(
            f"Backup created: {backup_id}",
            extra={
                "size_bytes": total_size,
                "checksum": db_checksum[:16] if db_checksum else "N/A",
            },
        )

        # Update registry manifest with backup info for DR tracking
        try:
            from libs.models.manifest import RegistryManifestManager

            manifest_manager = RegistryManifestManager(self.registry_dir)
            self.update_manifest_backup_info(manifest_manager)
        except Exception as e:
            # Non-fatal: backup succeeded, but manifest update failed
            logger.warning(f"Failed to update manifest with backup info: {e}")

        return backup_manifest

    def restore_from_backup(
        self, backup_date: date | None = None, backup_id: str | None = None
    ) -> RestoreResult:
        """Restore registry from backup.

        Args:
            backup_date: Date to restore from (finds closest backup).
            backup_id: Specific backup ID to restore.

        Returns:
            RestoreResult with restoration status.
        """
        backup_path: Path | None = None
        if backup_id:
            backup_path = self.backups_dir / backup_id
        elif backup_date:
            # Find closest backup
            backup_path = self._find_backup_by_date(backup_date)
        else:
            # Find most recent backup
            backup_path = self._find_most_recent_backup()

        if backup_path is None or not backup_path.exists():
            # Convert date to datetime if needed for RestoreResult
            result_date = (
                datetime.combine(backup_date, datetime.min.time(), tzinfo=UTC)
                if backup_date
                else datetime.now(UTC)
            )
            return RestoreResult(
                success=False,
                backup_date=result_date,
                restored_at=datetime.now(UTC),
                models_restored=0,
                message="No backup found",
            )

        logger.info(f"Restoring from backup: {backup_path}")

        # Load backup manifest
        manifest_path = backup_path / "backup_manifest.json"
        if manifest_path.exists():
            with open(manifest_path) as f:
                backup_manifest = json.load(f)
        else:
            backup_manifest = {}

        # Verify checksum
        db_path = backup_path / "registry.db"
        if db_path.exists() and backup_manifest.get("checksum"):
            actual_checksum = compute_checksum(db_path)
            if actual_checksum != backup_manifest["checksum"]:
                return RestoreResult(
                    success=False,
                    backup_date=datetime.fromisoformat(
                        backup_manifest.get("created_at", datetime.now(UTC).isoformat())
                    ),
                    restored_at=datetime.now(UTC),
                    models_restored=0,
                    message="Backup checksum verification failed",
                )

        # Acquire exclusive lock during restore to prevent serving half-restored state
        # This blocks other processes from reading the registry during restore
        with self._restore_lock():
            # Create backup of current state before attempting restore so we can roll back
            pre_restore_backup = self.create_backup()

            try:
                self._restore_backup_contents(backup_path)
            except Exception as restore_err:
                logger.error(
                    "Restore failed, attempting rollback to pre-restore snapshot",
                    extra={
                        "backup": str(backup_path),
                        "error": str(restore_err),
                        "pre_restore_backup": pre_restore_backup.backup_id,
                    },
                )
                rollback_success = False
                try:
                    self._restore_backup_contents(Path(pre_restore_backup.backup_path))
                    rollback_success = True
                    logger.info(
                        "Rolled back to pre-restore snapshot after failure",
                        extra={"backup": pre_restore_backup.backup_id},
                    )
                except Exception as rollback_err:
                    logger.critical(
                        "Rollback after failed restore also failed - manual intervention required",
                        extra={"error": str(rollback_err)},
                    )

                return RestoreResult(
                    success=False,
                    backup_date=datetime.fromisoformat(
                        backup_manifest.get("created_at", datetime.now(UTC).isoformat())
                    ),
                    restored_at=datetime.now(UTC),
                    models_restored=0,
                    message=(
                        f"Restore from {backup_path.name} failed: {restore_err}. "
                        + (
                            "Rollback to pre-restore snapshot succeeded."
                            if rollback_success
                            else "Rollback to pre-restore snapshot failed - registry may be inconsistent."
                        )
                    ),
                )

        # Count restored models (after lock released, safe to read)
        models_restored = 0
        if self.artifacts_dir.exists():
            for model_type_dir in self.artifacts_dir.iterdir():
                if model_type_dir.is_dir():
                    models_restored += len(list(model_type_dir.iterdir()))

        logger.info(
            f"Restore completed: {models_restored} models",
            extra={"backup_path": str(backup_path)},
        )

        return RestoreResult(
            success=True,
            backup_date=datetime.fromisoformat(
                backup_manifest.get("created_at", datetime.now(UTC).isoformat())
            ),
            restored_at=datetime.now(UTC),
            models_restored=models_restored,
            message=f"Restored from {backup_path.name}",
        )

    def _restore_backup_contents(self, backup_path: Path) -> None:
        """Restore registry.db, manifest, and artifacts from a backup directory."""
        db_path = backup_path / "registry.db"
        if db_path.exists():
            shutil.copy2(db_path, self.db_path)

        manifest_src = backup_path / "manifest.json"
        if manifest_src.exists():
            shutil.copy2(manifest_src, self.registry_dir / "manifest.json")

        artifacts_backup = backup_path / "artifacts"
        if artifacts_backup.exists():
            if self.artifacts_dir.exists():
                shutil.rmtree(self.artifacts_dir)
            shutil.copytree(artifacts_backup, self.artifacts_dir)

    def sync_to_remote(self, remote_path: str) -> SyncResult:
        """Sync backups to remote storage via rclone.

        Args:
            remote_path: Remote path (e.g., "s3:bucket/path/").

        Returns:
            SyncResult with sync status.
        """
        if not shutil.which("rclone"):
            return SyncResult(
                success=False,
                remote_path=remote_path,
                synced_at=datetime.now(UTC),
                bytes_transferred=0,
                message="rclone not installed",
            )

        try:
            # Run rclone sync
            result = subprocess.run(
                ["rclone", "sync", str(self.backups_dir), remote_path, "--verbose"],
                capture_output=True,
                text=True,
                timeout=3600,  # 1 hour timeout
            )

            if result.returncode != 0:
                return SyncResult(
                    success=False,
                    remote_path=remote_path,
                    synced_at=datetime.now(UTC),
                    bytes_transferred=0,
                    message=f"rclone failed: {result.stderr}",
                )

            # Parse transferred bytes from rclone output
            # Format: "Transferred:      1.234 GiB / 1.234 GiB, 100%, ..."
            bytes_transferred = 0
            for line in result.stdout.split("\n"):
                if "Transferred:" in line and "/" in line:
                    try:
                        # Extract the first size value (what was transferred)
                        parts = line.split(":")
                        if len(parts) >= 2:
                            size_part = parts[1].strip().split("/")[0].strip()
                            bytes_transferred = self._parse_rclone_size(size_part)
                    except (ValueError, IndexError) as e:
                        logger.debug(f"Failed to parse rclone bytes: {e}")

            logger.info(
                f"Synced backups to {remote_path}",
                extra={"bytes_transferred": bytes_transferred},
            )

            return SyncResult(
                success=True,
                remote_path=remote_path,
                synced_at=datetime.now(UTC),
                bytes_transferred=bytes_transferred,
                message="Sync completed",
            )

        except subprocess.TimeoutExpired:
            return SyncResult(
                success=False,
                remote_path=remote_path,
                synced_at=datetime.now(UTC),
                bytes_transferred=0,
                message="rclone sync timed out",
            )
        except Exception as e:
            return SyncResult(
                success=False,
                remote_path=remote_path,
                synced_at=datetime.now(UTC),
                bytes_transferred=0,
                message=f"Sync error: {e!s}",
            )

    def _parse_rclone_size(self, size_str: str) -> int:
        """Parse rclone size string to bytes.

        Args:
            size_str: Size string like "1.234 GiB", "500 MiB", "1024 B".

        Returns:
            Size in bytes.
        """
        size_str = size_str.strip()
        if not size_str:
            return 0

        # Define multipliers for common units
        multipliers = {
            "B": 1,
            "KiB": 1024,
            "MiB": 1024**2,
            "GiB": 1024**3,
            "TiB": 1024**4,
            "KB": 1000,
            "MB": 1000**2,
            "GB": 1000**3,
            "TB": 1000**4,
        }

        # Try to parse "value unit" format
        parts = size_str.split()
        if len(parts) >= 2:
            try:
                value = float(parts[0])
                unit = parts[1]
                if unit in multipliers:
                    return int(value * multipliers[unit])
            except ValueError:
                pass

        # If parsing fails, try to interpret as raw bytes
        try:
            return int(float(size_str))
        except ValueError:
            return 0

    def update_manifest_backup_info(
        self, manifest_manager: RegistryManifestManager
    ) -> None:
        """Update registry manifest with backup info.

        Args:
            manifest_manager: Registry manifest manager.
        """
        # Find most recent backup
        backup_path = self._find_most_recent_backup()
        if backup_path:
            backup_manifest_path = backup_path / "backup_manifest.json"
            if backup_manifest_path.exists():
                with open(backup_manifest_path) as f:
                    backup_info = json.load(f)
                manifest_manager.update_manifest(
                    last_backup_at=datetime.fromisoformat(backup_info["created_at"]),
                    backup_location=str(backup_path),
                )

    def cleanup_old_backups(self, retention_days: int | None = None) -> int:
        """Remove backups older than retention period.

        Args:
            retention_days: Override retention (default: 90 days).

        Returns:
            Number of backups removed.
        """
        retention = retention_days or self.BACKUP_RETENTION_DAYS
        cutoff = datetime.now(UTC) - timedelta(days=retention)
        count = 0

        if not self.backups_dir.exists():
            return 0

        for backup_path in self.backups_dir.iterdir():
            if not backup_path.is_dir():
                continue

            manifest_path = backup_path / "backup_manifest.json"
            if manifest_path.exists():
                with open(manifest_path) as f:
                    manifest = json.load(f)
                created_at = datetime.fromisoformat(manifest["created_at"])
                if created_at < cutoff:
                    shutil.rmtree(backup_path)
                    count += 1
                    logger.info(f"Removed old backup: {backup_path.name}")

        logger.info(f"Cleaned up {count} old backups (retention: {retention} days)")
        return count

    def _find_backup_by_date(self, target_date: date) -> Path | None:
        """Find backup closest to target date."""
        if not self.backups_dir.exists():
            return None

        best_backup = None
        best_diff = timedelta.max

        for backup_path in self.backups_dir.iterdir():
            if not backup_path.is_dir():
                continue

            manifest_path = backup_path / "backup_manifest.json"
            if manifest_path.exists():
                with open(manifest_path) as f:
                    manifest = json.load(f)
                created_at = datetime.fromisoformat(manifest["created_at"])
                diff = abs(created_at.date() - target_date)
                if diff < best_diff:
                    best_diff = diff
                    best_backup = backup_path

        return best_backup

    def _find_most_recent_backup(self) -> Path | None:
        """Find most recent backup."""
        if not self.backups_dir.exists():
            return None

        best_backup = None
        best_time = datetime.min.replace(tzinfo=UTC)

        for backup_path in self.backups_dir.iterdir():
            if not backup_path.is_dir():
                continue

            manifest_path = backup_path / "backup_manifest.json"
            if manifest_path.exists():
                with open(manifest_path) as f:
                    manifest = json.load(f)
                created_at = datetime.fromisoformat(manifest["created_at"])
                if created_at > best_time:
                    best_time = created_at
                    best_backup = backup_path

        return best_backup


# =============================================================================
# Garbage Collection
# =============================================================================


class RegistryGC:
    """Garbage collection for expired model artifacts.

    Retention policy:
    - Production models: indefinite
    - Staged models: 30 days
    - Archived models: 90 days
    """

    STAGED_RETENTION_DAYS = 30
    ARCHIVED_RETENTION_DAYS = 90

    def __init__(self, registry: ModelRegistry) -> None:
        """Initialize GC.

        Args:
            registry: Model registry instance.
        """
        self.registry = registry

    def collect_expired_staged(
        self, max_age_days: int | None = None
    ) -> list[str]:
        """Find staged models older than max age.

        Args:
            max_age_days: Override max age (default: 30 days).

        Returns:
            List of model IDs to collect.
        """
        from libs.models.types import ModelStatus

        max_age = max_age_days or self.STAGED_RETENTION_DAYS
        cutoff = datetime.now(UTC) - timedelta(days=max_age)
        expired = []

        models = self.registry.list_models(status=ModelStatus.staged)
        for model in models:
            if model.created_at < cutoff:
                expired.append(model.model_id)

        return expired

    def collect_expired_archived(
        self, max_age_days: int | None = None
    ) -> list[str]:
        """Find archived models older than max age based on archived_at timestamp.

        Uses archived_at from DB (when model was archived), not created_at.
        This ensures models that were in production for a long time still get
        proper retention after archival.

        Args:
            max_age_days: Override max age (default: 90 days).

        Returns:
            List of model IDs to collect.
        """
        max_age = max_age_days or self.ARCHIVED_RETENTION_DAYS
        cutoff = datetime.now(UTC) - timedelta(days=max_age)
        expired = []

        # Query DB directly to get archived_at timestamp (not available in ModelMetadata)
        with self.registry._get_connection(read_only=True) as conn:
            result = conn.execute(
                """
                SELECT model_id, archived_at
                FROM models
                WHERE status = 'archived' AND archived_at IS NOT NULL
                """
            ).fetchall()

        for row in result:
            model_id = row[0]
            archived_at_str = row[1]
            if archived_at_str:
                # Parse ISO format timestamp
                archived_at = datetime.fromisoformat(archived_at_str)
                # Ensure timezone-aware comparison
                if archived_at.tzinfo is None:
                    archived_at = archived_at.replace(tzinfo=UTC)
                if archived_at < cutoff:
                    expired.append(model_id)

        return expired

    def run_gc(self, dry_run: bool = True) -> GCReport:
        """Run garbage collection.

        Args:
            dry_run: If True, don't actually delete (default: True).

        Returns:
            GCReport with results.

        Raises:
            RegistryLockError: If restore is in progress (fail-fast).
        """
        # Fail fast if restore is in progress - don't delete during restore
        self.registry._check_restore_lock()

        expired_staged = self.collect_expired_staged()
        expired_archived = self.collect_expired_archived()

        bytes_freed = 0
        all_expired = expired_staged + expired_archived

        if not dry_run and all_expired:
            # Actually delete artifacts
            for model_id in all_expired:
                try:
                    # Get artifact path from DB (authoritative), not computed path
                    # This handles cases where artifacts were restored to different locations
                    with self.registry._get_connection(read_only=True) as conn:
                        result = conn.execute(
                            "SELECT artifact_path FROM models WHERE model_id = ?",
                            [model_id],
                        ).fetchone()

                    if result and result[0]:
                        artifact_path = Path(result[0])
                        if artifact_path.exists():
                            # Calculate size before deletion
                            for f in artifact_path.rglob("*"):
                                if f.is_file():
                                    bytes_freed += f.stat().st_size
                            # Delete artifact directory
                            shutil.rmtree(artifact_path)
                            logger.info(f"Deleted artifact: {artifact_path}")
                        else:
                            logger.warning(
                                f"Artifact path not found during GC: {artifact_path}"
                            )

                    # Remove from database
                    self._delete_model_from_db(model_id)

                except Exception as e:
                    logger.error(f"Failed to delete model {model_id}: {e}")

            # Update manifest after deletions
            self._update_manifest_after_gc()

        logger.info(
            f"GC run completed (dry_run={dry_run})",
            extra={
                "expired_staged": len(expired_staged),
                "expired_archived": len(expired_archived),
                "bytes_freed": bytes_freed,
            },
        )

        return GCReport(
            dry_run=dry_run,
            expired_staged=expired_staged,
            expired_archived=expired_archived,
            bytes_freed=bytes_freed,
            run_at=datetime.now(UTC),
        )

    def _update_manifest_after_gc(self) -> None:
        """Update manifest after GC deletions."""
        try:
            # Recalculate counts and update manifest
            self.registry._update_manifest_counts()
            self.registry._update_manifest_production()
            logger.info("Updated manifest after GC")
        except Exception as e:
            logger.error(f"Failed to update manifest after GC: {e}")

    def _delete_model_from_db(self, model_id: str) -> None:
        """Delete model entry from database.

        Uses registry's _get_connection() to respect restore lock.

        Args:
            model_id: Model ID to delete.

        Raises:
            RegistryLockError: If restore is in progress.
        """
        with self.registry._get_connection() as conn:
            conn.execute("DELETE FROM promotion_history WHERE model_id = ?", [model_id])
            conn.execute("DELETE FROM models WHERE model_id = ?", [model_id])
            logger.info(f"Deleted model from DB: {model_id}")

    def update_manifest_after_gc(
        self, manifest_manager: RegistryManifestManager
    ) -> None:
        """Update manifest after GC run.

        Args:
            manifest_manager: Registry manifest manager.
        """
        # Recalculate artifact count and size from disk to keep manifest accurate
        artifact_count = 0
        total_size = 0

        artifacts_dir = manifest_manager.registry_dir / "artifacts"
        if artifacts_dir.exists():
            for path in artifacts_dir.rglob("*"):
                if path.is_file():
                    artifact_count += 1 if path.name == "metadata.json" else 0
                    total_size += path.stat().st_size

        manifest_manager.update_manifest(
            artifact_count=artifact_count,
            total_size_bytes=total_size,
        )
