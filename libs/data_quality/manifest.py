"""
Sync manifest tracking for data quality and reproducibility.

This module provides:
- SyncManifest: Pydantic model tracking data sync state
- ManifestManager: Manager for atomic manifest operations with locking
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import shutil
import socket
import tempfile
import time
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, field_validator

from libs.data_quality.exceptions import (
    DiskSpaceError,
    LockNotHeldError,
    QuarantineError,
)
from libs.data_quality.types import DiskSpaceStatus, LockToken

logger = logging.getLogger(__name__)


class SyncManifest(BaseModel):
    """Tracks data sync state for reproducibility.

    This model captures all metadata needed to reproduce a data sync,
    verify data integrity, and support rollback on failure.

    Attributes:
        dataset: Dataset identifier (e.g., "crsp_daily").
        sync_timestamp: UTC timestamp when sync occurred.
        start_date: Data range start date.
        end_date: Data range end date.
        row_count: Total rows synced.
        checksum: SHA-256 checksum of parquet file(s).
        checksum_algorithm: Algorithm used (always "sha256").
        schema_version: Schema version string (e.g., "v1.0.0").
        wrds_query_hash: Hash of SQL query used for sync.
        file_paths: List of parquet files included.
        validation_status: Current validation status.
        quarantine_path: Path if data was quarantined.
        manifest_version: Version number (increments on update).
        previous_checksum: Previous checksum for rollback verification.
    """

    # Dataset identification
    dataset: str

    # Sync metadata
    sync_timestamp: datetime
    start_date: date
    end_date: date

    # Data integrity
    row_count: int
    checksum: str
    checksum_algorithm: Literal["sha256"] = "sha256"

    # Schema tracking
    schema_version: str
    wrds_query_hash: str

    # File tracking
    file_paths: list[str]

    # Validation status
    validation_status: Literal["passed", "failed", "quarantined"]
    quarantine_path: str | None = None

    # Versioning (for rollback)
    manifest_version: int = 1
    previous_checksum: str | None = None

    model_config = {"frozen": False}

    @field_validator("sync_timestamp")
    @classmethod
    def validate_utc(cls, v: datetime) -> datetime:
        """Ensure timestamp is UTC (offset == 0), not just timezone-aware."""
        if v.tzinfo is None:
            raise ValueError("sync_timestamp must be timezone-aware")
        offset = v.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError("sync_timestamp must be UTC (offset == 0)")
        return v

    @field_validator("end_date")
    @classmethod
    def validate_date_range(cls, v: date, info: Any) -> date:
        """Ensure start_date <= end_date."""
        if hasattr(info, "data") and "start_date" in info.data and v < info.data["start_date"]:
            raise ValueError("end_date must be >= start_date")
        return v

    @field_validator("file_paths")
    @classmethod
    def validate_non_empty(cls, v: list[str]) -> list[str]:
        """Ensure file_paths is non-empty."""
        if not v:
            raise ValueError("file_paths must not be empty")
        return v


class ManifestManager:
    """Manages sync manifests with atomic writes and lock coupling.

    This manager ensures manifest operations are atomic and safe:
    - All writes require holding an exclusive lock
    - Disk space is checked before writes
    - Atomic write pattern: temp file -> verify -> rename -> fsync
    - Supports rollback on failure

    Attributes:
        storage_path: Directory for manifest files.
        lock_dir: Directory for lock files.
        backup_dir: Directory for manifest backups.
        quarantine_dir: Directory for quarantined data.
    """

    DATA_ROOT = Path("data")  # Root directory for all data operations
    MANIFEST_DIR = Path("data/manifests")
    LOCK_DIR = Path("data/locks")
    BACKUP_DIR = Path("data/manifests/backups")
    QUARANTINE_DIR = Path("data/quarantine")

    REQUIRED_DISK_MULTIPLIER = 2.0  # >= 2x expected write size
    DISK_WARNING_THRESHOLD = 0.80  # 80% capacity
    DISK_CRITICAL_THRESHOLD = 0.90  # 90% capacity
    DISK_BLOCKED_THRESHOLD = 0.95  # 95% - refuse writes

    LOCK_MAX_AGE_HOURS = 4  # Locks expire after 4 hours
    LOCK_STALE_MINUTES = 5  # Lock considered stale if mtime > 5 min old
    LOCK_STALE_SECONDS = 300  # 5 minutes - check PID liveness after this
    LOCK_HARD_TIMEOUT_SECONDS = 1800  # 30 minutes - break any lock after this

    def __init__(
        self,
        storage_path: Path | None = None,
        lock_dir: Path | None = None,
        backup_dir: Path | None = None,
        quarantine_dir: Path | None = None,
        data_root: Path | None = None,
    ) -> None:
        """Initialize manifest manager.

        Args:
            storage_path: Directory for manifest files.
            lock_dir: Directory for lock files.
            backup_dir: Directory for manifest backups.
            quarantine_dir: Directory for quarantined data.
            data_root: Root directory for file path validation (security).
        """
        self.storage_path = storage_path or self.MANIFEST_DIR
        self.lock_dir = lock_dir or self.LOCK_DIR
        self.backup_dir = backup_dir or self.BACKUP_DIR
        self.quarantine_dir = quarantine_dir or self.QUARANTINE_DIR
        self.data_root = data_root or self.DATA_ROOT

        # Create directories
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        self.quarantine_dir.mkdir(parents=True, exist_ok=True)

    def _sanitize_dataset(self, dataset: str) -> str:
        """Sanitize dataset name to prevent path traversal.

        Args:
            dataset: Raw dataset identifier.

        Returns:
            Sanitized dataset name (basename only, no path components).

        Raises:
            ValueError: If dataset name is empty after sanitization.
        """
        # Extract basename to prevent path traversal (../ attacks)
        sanitized = Path(dataset).name
        if not sanitized or sanitized in (".", ".."):
            raise ValueError(f"Invalid dataset name: {dataset!r}")
        return sanitized

    def _validate_file_path(self, file_path: Path) -> bool:
        """Validate that a file path is within the permitted data directory.

        This prevents arbitrary file access/movement if manifest data is compromised.

        Args:
            file_path: Path to validate.

        Returns:
            True if path is within data root, False otherwise.
        """
        try:
            resolved = file_path.resolve()
            data_root = self.data_root.resolve()
            return resolved.is_relative_to(data_root)
        except (ValueError, OSError):
            return False

    def _manifest_path(self, dataset: str) -> Path:
        """Get manifest file path for dataset."""
        return self.storage_path / f"{self._sanitize_dataset(dataset)}.json"

    def _lock_path(self, dataset: str) -> Path:
        """Get lock file path for dataset."""
        return self.lock_dir / f"{self._sanitize_dataset(dataset)}.lock"

    def _backup_path(self, dataset: str, version: int) -> Path:
        """Get backup file path for dataset and version."""
        return self.backup_dir / f"{self._sanitize_dataset(dataset)}_v{version}.json"

    @contextmanager
    def acquire_lock(
        self,
        dataset: str,
        writer_id: str,
        timeout_seconds: float = 30.0,
        retry_interval: float = 0.1,
    ) -> Generator[LockToken, None, None]:
        """Acquire exclusive lock for manifest operations.

        Uses O_CREAT | O_EXCL for atomic lock file creation.
        Implements stale lock detection with PID liveness checks
        and hard timeout for remote locks.

        Args:
            dataset: Dataset to lock.
            writer_id: Identifier for the locking process.
            timeout_seconds: Max time to wait for lock.
            retry_interval: Time between retry attempts.

        Yields:
            LockToken proving lock ownership.

        Raises:
            LockNotHeldError: If lock cannot be acquired within timeout.
        """
        lock_path = self._lock_path(dataset)
        now = datetime.now(UTC)
        pid = os.getpid()
        hostname = socket.gethostname()
        lock_data = {
            "pid": pid,
            "hostname": hostname,
            "writer_id": writer_id,
            "acquired_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=self.LOCK_MAX_AGE_HOURS)).isoformat(),
        }

        start_time = time.monotonic()
        acquired = False

        while time.monotonic() - start_time < timeout_seconds:
            try:
                # Atomic create with O_CREAT | O_EXCL
                fd = os.open(
                    lock_path,
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o644,
                )
                try:
                    os.write(fd, json.dumps(lock_data).encode())
                    os.fsync(fd)
                finally:
                    os.close(fd)
                acquired = True
                break
            except FileExistsError:
                # Lock held by another process, check if stale
                self._try_break_stale_lock(lock_path, dataset)
                time.sleep(retry_interval)

        if not acquired:
            raise LockNotHeldError(
                f"Failed to acquire lock for {dataset} within {timeout_seconds}s"
            )

        # Create token
        token = LockToken(
            pid=pid,
            hostname=hostname,
            writer_id=writer_id,
            acquired_at=now,
            expires_at=now + timedelta(hours=self.LOCK_MAX_AGE_HOURS),
            lock_path=lock_path,
        )

        try:
            yield token
        finally:
            # Release lock - use fcntl.flock for atomic read-verify-delete
            self._release_lock(lock_path, lock_data, dataset)

    def _try_break_stale_lock(self, lock_path: Path, dataset: str) -> None:
        """Attempt to break a stale lock.

        Uses hard timeout (30 min) and PID liveness checks.
        """
        try:
            if not lock_path.exists():
                return

            mtime = lock_path.stat().st_mtime
            age_seconds = time.time() - mtime

            # Hard timeout: break ANY lock older than 30 minutes
            if age_seconds > self.LOCK_HARD_TIMEOUT_SECONDS:
                logger.warning(
                    "Breaking stale lock for %s "
                    "(%.1f seconds old, exceeds hard timeout of %d seconds)",
                    dataset,
                    age_seconds,
                    self.LOCK_HARD_TIMEOUT_SECONDS,
                )
                lock_path.unlink(missing_ok=True)
                return

            # Soft timeout: check PID liveness after 5 minutes
            if age_seconds > self.LOCK_STALE_SECONDS:
                try:
                    with open(lock_path) as f:
                        existing_lock = json.load(f)
                    owner_pid = existing_lock.get("pid")
                    owner_hostname = existing_lock.get("hostname")
                    current_hostname = socket.gethostname()

                    # Only check PID if lock is from same host
                    if owner_hostname == current_hostname:
                        process_alive = False
                        if owner_pid:
                            try:
                                os.kill(owner_pid, 0)
                                process_alive = True
                            except (OSError, ProcessLookupError):
                                process_alive = False

                        if not process_alive:
                            logger.warning(
                                "Removing stale lock for %s "
                                "(%.1f seconds old, owner PID %s not alive)",
                                dataset,
                                age_seconds,
                                owner_pid,
                            )
                            lock_path.unlink(missing_ok=True)
                    else:
                        # Remote lock - wait for hard timeout
                        logger.debug(
                            "Lock for %s is from remote host %s, waiting for hard timeout",
                            dataset,
                            owner_hostname,
                        )
                except (json.JSONDecodeError, OSError):
                    # Can't read lock file, remove it
                    logger.warning("Removing unreadable lock for %s", dataset)
                    lock_path.unlink(missing_ok=True)
        except OSError:
            pass

    def _release_lock(self, lock_path: Path, lock_data: dict[str, Any], dataset: str) -> None:
        """Release lock with atomic verification.

        Uses fcntl.flock for TOCTOU-safe read-verify-delete.
        """
        try:
            if not lock_path.exists():
                return

            fd = os.open(lock_path, os.O_RDONLY)
            try:
                # Acquire exclusive flock to prevent concurrent modification
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                try:
                    # Read and verify ownership while holding flock
                    with os.fdopen(os.dup(fd), "r") as f:
                        current_lock = json.load(f)

                    # Only delete if we still own the lock
                    if (
                        current_lock.get("pid") == lock_data["pid"]
                        and current_lock.get("hostname") == lock_data["hostname"]
                        and current_lock.get("writer_id") == lock_data["writer_id"]
                    ):
                        lock_path.unlink(missing_ok=True)
                        logger.debug("Released lock for dataset")
                    else:
                        logger.warning(
                            "Lock for %s was taken over by another process, not releasing",
                            dataset,
                        )
                finally:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            except BlockingIOError:
                # Another process holds the flock - don't delete
                logger.debug("Lock for %s is held by another process", dataset)
            finally:
                os.close(fd)
        except (json.JSONDecodeError, OSError) as e:
            # Lock file unreadable or gone - safe to ignore
            logger.debug("Could not release lock for %s: %s", dataset, e)

    def load_manifest(self, dataset: str) -> SyncManifest | None:
        """Load manifest for dataset.

        Args:
            dataset: Dataset identifier.

        Returns:
            SyncManifest if found, None otherwise.
        """
        path = self._manifest_path(dataset)
        if not path.exists():
            return None

        with open(path) as f:
            data = json.load(f)

        return SyncManifest.model_validate(data)

    def save_manifest(
        self,
        manifest: SyncManifest,
        lock_token: LockToken,
        expected_bytes: int | None = None,
    ) -> None:
        """Save manifest with atomic write.

        This method:
        1. Verifies caller holds exclusive lock
        2. Checks disk space
        3. Backs up current manifest if exists
        4. Atomically writes new manifest

        Args:
            manifest: The manifest to save.
            lock_token: Token proving exclusive lock ownership.
            expected_bytes: Expected size of data files for disk check.
                           If None, calculates from manifest.file_paths sizes.

        Raises:
            LockNotHeldError: If lock not held or expired.
            DiskSpaceError: If insufficient disk space.
            QuarantineError: If write fails (ENOSPC).
        """
        # Verify lock
        self.assert_lock_held(lock_token)

        # Verify lock is for the correct dataset (prevent cross-dataset lock reuse)
        expected_lock_path = self._lock_path(manifest.dataset)
        if lock_token.lock_path != expected_lock_path:
            raise LockNotHeldError(
                f"Lock path mismatch: lock is for {lock_token.lock_path}, "
                f"but saving manifest for dataset '{manifest.dataset}' "
                f"(expected lock: {expected_lock_path})"
            )

        # Verify all referenced files exist and have non-zero size
        # This prevents "signing off" on missing or corrupt data
        for file_path_str in manifest.file_paths:
            file_path = Path(file_path_str)
            if not file_path.exists():
                raise QuarantineError(
                    f"Data integrity error: file '{file_path_str}' does not exist. "
                    f"Cannot save manifest with missing files."
                )
            if file_path.stat().st_size == 0:
                raise QuarantineError(
                    f"Data integrity error: file '{file_path_str}' has zero size. "
                    f"Cannot save manifest with empty files."
                )

        # Prepare manifest data
        manifest_json = manifest.model_dump_json(indent=2)
        manifest_bytes = len(manifest_json.encode())

        # Check disk space - use expected_bytes or calculate from file sizes
        if expected_bytes is not None:
            required = expected_bytes
        else:
            # Calculate actual file sizes from manifest.file_paths
            total_file_size = 0
            for file_path_str in manifest.file_paths:
                file_path = Path(file_path_str)
                if file_path.exists():
                    total_file_size += file_path.stat().st_size
            # Use file sizes if available, otherwise fall back to manifest size
            required = total_file_size if total_file_size > 0 else manifest_bytes

        disk_status = self.check_disk_space(int(required * self.REQUIRED_DISK_MULTIPLIER))

        if disk_status.level == "warning":
            logger.warning(
                "Disk space warning: %s (%.1f%% used)",
                disk_status.message,
                disk_status.used_pct * 100,
            )
        elif disk_status.level == "critical":
            logger.critical(
                "Disk space critical: %s (%.1f%% used)",
                disk_status.message,
                disk_status.used_pct * 100,
            )

        # Load current manifest for backup and versioning
        current = self.load_manifest(manifest.dataset)
        if current:
            # Create backup
            backup_path = self._backup_path(manifest.dataset, current.manifest_version)
            self._atomic_write(backup_path, current.model_dump())

            # Update version and previous checksum
            manifest.manifest_version = current.manifest_version + 1
            manifest.previous_checksum = current.checksum

        # Atomic write new manifest
        manifest_path = self._manifest_path(manifest.dataset)
        try:
            self._atomic_write(manifest_path, manifest.model_dump())
        except OSError as e:
            if e.errno == 28:  # ENOSPC
                logger.error("Disk full during manifest write: %s", e)
                raise DiskSpaceError(f"Disk full during manifest write: {e}") from e
            raise

    def assert_lock_held(self, lock_token: LockToken) -> None:
        """Verify caller holds valid exclusive lock.

        Checks:
        - Lock file exists
        - Lock token matches (pid, hostname, writer_id)
        - Lock not expired (< 4 hours)
        - Lock file freshness (mtime recent)

        Args:
            lock_token: Token to validate.

        Raises:
            LockNotHeldError: If any check fails.
        """
        lock_path = lock_token.lock_path

        # Check lock file exists
        if not lock_path.exists():
            raise LockNotHeldError(f"Lock file does not exist: {lock_path}")

        # Read lock file
        try:
            with open(lock_path) as f:
                lock_data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            raise LockNotHeldError(f"Failed to read lock file: {e}") from e

        # Verify token matches
        if lock_data.get("pid") != lock_token.pid:
            raise LockNotHeldError(
                f"Lock PID mismatch: expected {lock_token.pid}, got {lock_data.get('pid')}"
            )
        if lock_data.get("hostname") != lock_token.hostname:
            raise LockNotHeldError(
                f"Lock hostname mismatch: expected {lock_token.hostname}, "
                f"got {lock_data.get('hostname')}"
            )
        if lock_data.get("writer_id") != lock_token.writer_id:
            raise LockNotHeldError(
                f"Lock writer_id mismatch: expected {lock_token.writer_id}, "
                f"got {lock_data.get('writer_id')}"
            )

        # Check expiration from file (single source of truth)
        now = datetime.now(UTC)
        file_expires_at_str = lock_data.get("expires_at")
        if file_expires_at_str:
            file_expires_at = datetime.fromisoformat(file_expires_at_str)
            if now > file_expires_at:
                raise LockNotHeldError(f"Lock expired at {file_expires_at.isoformat()}")

        # Enforce hard timeout based on acquired_at (30 min max)
        # This prevents operations from running beyond the hard limit
        # even if expires_at was extended by refresh_lock
        acquired_at_str = lock_data.get("acquired_at")
        if acquired_at_str:
            acquired_at = datetime.fromisoformat(acquired_at_str)
            hard_deadline = acquired_at + timedelta(seconds=self.LOCK_HARD_TIMEOUT_SECONDS)
            if now > hard_deadline:
                raise LockNotHeldError(
                    f"Lock exceeded hard timeout: acquired at {acquired_at.isoformat()}, "
                    f"hard deadline was {hard_deadline.isoformat()}. "
                    f"Release and re-acquire the lock."
                )

        # Check mtime freshness (stale lock detection)
        mtime = datetime.fromtimestamp(lock_path.stat().st_mtime, tz=UTC)
        age_minutes = (datetime.now(UTC) - mtime).total_seconds() / 60
        if age_minutes > self.LOCK_STALE_MINUTES:
            logger.warning(
                "Lock file is stale (%.1f minutes old), but still valid",
                age_minutes,
            )

    def refresh_lock(self, lock_token: LockToken) -> LockToken:
        """Refresh lock to prevent expiration during long operations.

        Updates the lock file's mtime and extends expires_at.
        Call this periodically during long-running operations to keep
        the lock alive.

        IMPORTANT: Enforces hard timeout based on acquired_at. Once
        acquired_at + LOCK_HARD_TIMEOUT_SECONDS is exceeded, refresh
        is refused and lock must be released.

        Args:
            lock_token: Current lock token to refresh.

        Returns:
            Updated LockToken with new expires_at.

        Raises:
            LockNotHeldError: If lock is no longer held or hard timeout exceeded.
        """
        # First verify we still own the lock
        self.assert_lock_held(lock_token)

        lock_path = lock_token.lock_path
        now = datetime.now(UTC)

        # Read lock file to get acquired_at
        try:
            with open(lock_path) as f:
                lock_data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            raise LockNotHeldError(f"Failed to read lock file: {e}") from e

        # Enforce hard timeout based on acquired_at
        acquired_at_str = lock_data.get("acquired_at")
        if acquired_at_str:
            acquired_at = datetime.fromisoformat(acquired_at_str)
            hard_deadline = acquired_at + timedelta(seconds=self.LOCK_HARD_TIMEOUT_SECONDS)
            if now > hard_deadline:
                raise LockNotHeldError(
                    f"Lock exceeded hard timeout: acquired at {acquired_at.isoformat()}, "
                    f"hard deadline was {hard_deadline.isoformat()}. "
                    f"Release and re-acquire the lock."
                )

        # Extend expiration (but not past hard deadline)
        new_expires_at = now + timedelta(hours=self.LOCK_MAX_AGE_HOURS)

        # Update lock file with new expiration (preserve acquired_at)
        lock_data["expires_at"] = new_expires_at.isoformat()

        # Atomic write to update lock file
        self._atomic_write(lock_path, lock_data)

        # Update and return new token
        lock_token.expires_at = new_expires_at

        logger.debug(
            "Refreshed lock for dataset, new expiry: %s",
            new_expires_at.isoformat(),
        )

        return lock_token

    def check_disk_space(self, required_bytes: int) -> DiskSpaceStatus:
        """Verify sufficient disk space for write.

        Policy:
        - Required: >= 2x expected write size
        - 80% capacity: WARNING logged, operation proceeds
        - 90% capacity: CRITICAL logged, operation proceeds with alert
        - 95% capacity: BLOCKED, raises DiskSpaceError

        Args:
            required_bytes: Bytes needed for the write operation.

        Returns:
            DiskSpaceStatus with level and space info.

        Raises:
            DiskSpaceError: If blocked threshold (95%) exceeded.
        """
        stat = shutil.disk_usage(self.storage_path)
        used_pct = (stat.total - stat.free) / stat.total

        if used_pct >= self.DISK_BLOCKED_THRESHOLD:
            raise DiskSpaceError(
                f"Disk space blocked: {used_pct:.1%} used, "
                f"threshold is {self.DISK_BLOCKED_THRESHOLD:.0%}"
            )

        if stat.free < required_bytes:
            raise DiskSpaceError(
                f"Insufficient disk space: need {required_bytes} bytes, "
                f"only {stat.free} available"
            )

        if used_pct >= self.DISK_CRITICAL_THRESHOLD:
            level: Literal["ok", "warning", "critical"] = "critical"
            message = f"Critical: {used_pct:.1%} disk usage"
        elif used_pct >= self.DISK_WARNING_THRESHOLD:
            level = "warning"
            message = f"Warning: {used_pct:.1%} disk usage"
        else:
            level = "ok"
            message = f"OK: {used_pct:.1%} disk usage"

        return DiskSpaceStatus(
            level=level,
            free_bytes=stat.free,
            total_bytes=stat.total,
            used_pct=used_pct,
            message=message,
        )

    def rollback_on_failure(
        self,
        dataset: str,
        lock_token: LockToken,
    ) -> SyncManifest | None:
        """Restore previous manifest version on sync failure.

        Rollback mechanics:
        1. Verify caller holds exclusive lock
        2. Load current manifest (if exists)
        3. Check previous_checksum is set (has rollback target)
        4. Load backup manifest
        5. Verify backup checksum matches previous_checksum
        6. Atomic replace current manifest with backup

        Args:
            dataset: Dataset to rollback.
            lock_token: Token proving exclusive lock ownership.

        Returns:
            Restored manifest or None if no rollback possible.

        Raises:
            LockNotHeldError: If lock not held or expired.
        """
        self.assert_lock_held(lock_token)

        # Verify lock is for the correct dataset (prevent cross-dataset lock reuse)
        expected_lock_path = self._lock_path(dataset)
        if lock_token.lock_path != expected_lock_path:
            raise LockNotHeldError(
                f"Lock path mismatch: lock is for {lock_token.lock_path}, "
                f"but rolling back dataset '{dataset}' "
                f"(expected lock: {expected_lock_path})"
            )

        current = self.load_manifest(dataset)
        if not current:
            logger.warning("No manifest to rollback for dataset: %s", dataset)
            return None

        if not current.previous_checksum:
            logger.warning("No previous version to rollback to for dataset: %s", dataset)
            return None

        # Find backup with matching checksum
        backup_version = current.manifest_version - 1
        backup_path = self._backup_path(dataset, backup_version)

        if not backup_path.exists():
            logger.error("Backup file not found: %s", backup_path)
            return None

        with open(backup_path) as f:
            backup_data = json.load(f)

        backup = SyncManifest.model_validate(backup_data)

        # Verify checksum matches
        if backup.checksum != current.previous_checksum:
            logger.error(
                "Backup checksum mismatch: expected %s, got %s",
                current.previous_checksum,
                backup.checksum,
            )
            return None

        # Restore backup
        manifest_path = self._manifest_path(dataset)
        self._atomic_write(manifest_path, backup_data)

        logger.info(
            "Rolled back %s from v%d to v%d",
            dataset,
            current.manifest_version,
            backup.manifest_version,
        )

        return backup

    def quarantine_data(
        self,
        manifest: SyncManifest,
        reason: str,
        lock_token: LockToken,
    ) -> str:
        """Move failed sync data to quarantine.

        Quarantine path: data/quarantine/{dataset}/{timestamp}/

        This method:
        1. Creates quarantine directory structure
        2. Moves all parquet files from manifest.file_paths
        3. Writes manifest.json with updated paths
        4. Writes reason.txt with quarantine details

        Args:
            manifest: Manifest of data to quarantine.
            reason: Reason for quarantine.
            lock_token: Token proving exclusive lock ownership.

        Returns:
            Quarantine path where data was moved.

        Raises:
            QuarantineError: If quarantine operation fails.
            LockNotHeldError: If lock not held.
        """
        self.assert_lock_held(lock_token)

        # Verify lock is for the correct dataset (prevent cross-dataset lock reuse)
        expected_lock_path = self._lock_path(manifest.dataset)
        if lock_token.lock_path != expected_lock_path:
            raise LockNotHeldError(
                f"Lock path mismatch: lock is for {lock_token.lock_path}, "
                f"but quarantining dataset '{manifest.dataset}' "
                f"(expected lock: {expected_lock_path})"
            )

        timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        quarantine_path = self.quarantine_dir / manifest.dataset / timestamp
        # Use staging directory for atomic quarantine operation
        staging_path = self.quarantine_dir / manifest.dataset / f".staging_{timestamp}"

        try:
            # Stage 0: Validate and resolve all file paths ONCE to prevent TOCTOU attacks
            # Resolve paths immediately and store them - don't re-read from manifest later
            original_paths = manifest.file_paths.copy()
            validated_files: list[tuple[Path, str]] = []  # (resolved_path, original_str)
            missing_files: list[str] = []

            for file_path_str in manifest.file_paths:
                src_path = Path(file_path_str)
                if src_path.exists():
                    # Resolve to canonical path NOW to prevent symlink swaps
                    resolved_path = src_path.resolve()
                    if not self._validate_file_path(resolved_path):
                        raise QuarantineError(
                            f"Security violation: file path '{file_path_str}' "
                            f"(resolved: {resolved_path}) is outside "
                            f"permitted data directory '{self.data_root}'"
                        )
                    validated_files.append((resolved_path, file_path_str))
                else:
                    missing_files.append(file_path_str)

            # Stage 1: Create staging directory and copy/link validated files
            staging_path.mkdir(parents=True, exist_ok=True)
            staging_data = staging_path / "data"
            staging_data.mkdir(parents=True, exist_ok=True)

            staged_paths: list[str] = []
            files_to_delete: list[Path] = []

            # Use pre-validated resolved paths to prevent TOCTOU
            for resolved_path, _original_str in validated_files:
                # Use unique destination name to prevent basename collisions
                # e.g., data/2024-01/part-0.parquet and data/2024-02/part-0.parquet
                # would otherwise both become part-0.parquet
                path_hash = hashlib.sha256(str(resolved_path).encode()).hexdigest()[:8]
                unique_name = f"{path_hash}_{resolved_path.name}"
                dest_path = staging_data / unique_name
                # Use hard link for zero-copy (instant, no disk space)
                # Fall back to copy if cross-device or hard links not supported
                try:
                    os.link(str(resolved_path), str(dest_path))
                    logger.debug("Hard-linked %s to %s", resolved_path, dest_path)
                except OSError:
                    # Cross-device link or not supported - fall back to copy
                    shutil.copy2(str(resolved_path), str(dest_path))
                    logger.debug("Copied %s to %s (hard link failed)", resolved_path, dest_path)
                # Track for final path calculation
                final_path = quarantine_path / "data" / unique_name
                staged_paths.append(str(final_path))
                files_to_delete.append(resolved_path)

            # Add missing files to staged_paths
            for missing_str in missing_files:
                logger.warning("File not found during quarantine: %s", missing_str)
                staged_paths.append(f"MISSING:{missing_str}")

            # Stage 2: Write manifest and reason files to staging
            manifest.quarantine_path = str(quarantine_path)
            manifest.validation_status = "quarantined"
            manifest.file_paths = staged_paths

            manifest_file = staging_path / "manifest.json"
            with open(manifest_file, "w") as f:
                f.write(manifest.model_dump_json(indent=2))
                f.flush()
                os.fsync(f.fileno())

            reason_file = staging_path / "reason.txt"
            with open(reason_file, "w") as f:
                f.write(f"Quarantined at: {timestamp}\n")
                f.write(f"Reason: {reason}\n")
                f.write("\nOriginal file paths:\n")
                for path in original_paths:
                    f.write(f"  - {path}\n")
                f.flush()
                os.fsync(f.fileno())

            # Stage 3: Atomic rename staging to final quarantine path
            # This is the commit point - after this, quarantine is visible
            staging_path.rename(quarantine_path)

            # Stage 4: Delete original files (quarantine is now complete)
            # If this fails, quarantine is still valid - originals just remain
            for src_path in files_to_delete:
                try:
                    src_path.unlink()
                    logger.debug("Deleted original: %s", src_path)
                except OSError as e:
                    logger.warning(
                        "Failed to delete original file %s after quarantine: %s",
                        src_path,
                        e,
                    )

            logger.warning(
                "Quarantined data for %s: %s (reason: %s, files: %d)",
                manifest.dataset,
                quarantine_path,
                reason,
                len(staged_paths),
            )

            return str(quarantine_path)

        except OSError as e:
            # Clean up staging directory on failure
            if staging_path.exists():
                try:
                    shutil.rmtree(staging_path)
                except OSError:
                    pass
            raise QuarantineError(f"Failed to quarantine data: {e}") from e

    def list_manifests(self) -> list[SyncManifest]:
        """List all manifests, ordered by dataset name.

        Returns:
            List of SyncManifest objects.
        """
        manifests = []
        for path in sorted(self.storage_path.glob("*.json")):
            if path.name.startswith("_"):  # Skip internal files
                continue
            try:
                with open(path) as f:
                    data = json.load(f)
                manifests.append(SyncManifest.model_validate(data))
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning("Failed to load manifest %s: %s", path, e)

        return manifests

    def create_snapshot(self, version_tag: str) -> None:
        """Create immutable snapshot of all current manifests.

        Args:
            version_tag: Tag for the snapshot (e.g., "2024-01-15").

        Raises:
            ValueError: If snapshot with tag already exists.
        """
        snapshot_dir = self.storage_path / "snapshots" / version_tag
        if snapshot_dir.exists():
            raise ValueError(f"Snapshot already exists: {version_tag}")

        snapshot_dir.mkdir(parents=True)

        for manifest_file in self.storage_path.glob("*.json"):
            shutil.copy2(manifest_file, snapshot_dir / manifest_file.name)

        logger.info("Created snapshot: %s", version_tag)

    def _atomic_write(self, path: Path, data: dict[str, Any]) -> None:
        """Atomic write with temp+fsync pattern.

        Args:
            path: Target file path.
            data: Data to write as JSON.
        """
        # Write to temp file in same directory (for atomic rename)
        fd, temp_path = tempfile.mkstemp(
            suffix=".tmp",
            prefix=path.stem + "_",
            dir=path.parent,
        )

        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2, default=str)
                f.flush()
                os.fsync(f.fileno())

            # Atomic rename
            Path(temp_path).rename(path)

            # Fsync parent directory
            dir_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)

        except Exception:
            # Clean up temp file on failure
            if Path(temp_path).exists():
                Path(temp_path).unlink()
            raise
