"""
OS-atomic file locking for single-writer data access.

This module implements:
- AtomicFileLock: Lock using O_CREAT|O_EXCL with stale recovery
- atomic_lock: Context manager for scoped locking
- Deterministic winner selection for concurrent recovery attempts

Lock file format (JSON):
{
    "pid": 12345,
    "hostname": "worker-01",
    "writer_id": "sync-job-abc123",
    "acquired_at": "2025-01-15T10:30:00+00:00",
    "expires_at": "2025-01-15T14:30:00+00:00"
}
"""

from __future__ import annotations

import datetime
import json
import logging
import os
import socket
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from libs.data_quality.exceptions import LockNotHeldError
from libs.data_quality.types import LockToken

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Expected fields in lock file - reject if extra fields present
_LOCK_FIELDS = {"pid", "hostname", "writer_id", "acquired_at", "expires_at"}


class LockAcquisitionError(Exception):
    """Failed to acquire lock within timeout."""

    pass


class LockRecoveryError(Exception):
    """Failed to recover stale lock."""

    pass


class MalformedLockFileError(Exception):
    """Lock file has invalid JSON or unexpected fields."""

    pass


class AtomicFileLock:
    """OS-atomic file lock using O_CREAT|O_EXCL for single-writer access.

    Uses atomic file creation to ensure only one writer can hold the lock.
    Implements stale lock recovery with deterministic winner selection via
    atomic rename.

    Attributes:
        LOCK_TIMEOUT_HOURS: Maximum lock duration before considered stale.
        REFRESH_INTERVAL_SECONDS: How often to refresh lock during long ops.
    """

    LOCK_TIMEOUT_HOURS = 4
    REFRESH_INTERVAL_SECONDS = 60
    RECOVERY_BACKOFF_SECONDS = [0.1, 0.5, 1.0, 2.0, 5.0]

    def __init__(
        self,
        lock_dir: Path,
        dataset: str,
        writer_id: str | None = None,
    ) -> None:
        """Initialize lock manager.

        Args:
            lock_dir: Directory for lock files (e.g., data/locks/).
            dataset: Dataset name (e.g., "crsp", "compustat").
            writer_id: Unique writer identifier. Defaults to UUID4.
        """
        self.lock_dir = Path(lock_dir)
        self.dataset = dataset
        self.writer_id = writer_id or str(uuid.uuid4())
        self.lock_path = self.lock_dir / f"{dataset}.lock"
        self._current_token: LockToken | None = None

    def acquire(self, timeout_seconds: float = 30.0) -> LockToken:
        """Acquire exclusive lock.

        Attempts atomic lock creation. If lock exists, checks for staleness
        and attempts recovery. Retries with backoff until timeout.

        Args:
            timeout_seconds: Maximum time to wait for lock acquisition.

        Returns:
            LockToken proving lock ownership.

        Raises:
            LockAcquisitionError: If lock cannot be acquired within timeout.
        """
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        start_time = time.monotonic()
        attempt = 0

        while time.monotonic() - start_time < timeout_seconds:
            try:
                token = self._try_acquire()
                self._current_token = token
                logger.info(
                    "Lock acquired",
                    extra={
                        "event": "sync.lock.acquired",
                        "dataset": self.dataset,
                        "writer_id": self.writer_id,
                    },
                )
                return token
            except FileExistsError:
                # Lock file exists, check if stale
                is_stale, reason = self._is_lock_stale(self.lock_path)
                if is_stale:
                    logger.warning(
                        "Stale lock detected",
                        extra={
                            "event": "sync.lock.stale",
                            "dataset": self.dataset,
                            "reason": reason,
                        },
                    )
                    if self._recover_stale_lock(self.lock_path):
                        continue  # Retry acquisition after recovery

                # Backoff and retry
                backoff_idx = min(attempt, len(self.RECOVERY_BACKOFF_SECONDS) - 1)
                backoff = self.RECOVERY_BACKOFF_SECONDS[backoff_idx]
                time.sleep(backoff)
                attempt += 1

        raise LockAcquisitionError(
            f"Failed to acquire lock for {self.dataset} within {timeout_seconds}s"
        )

    def release(self, token: LockToken) -> None:
        """Release exclusive lock.

        Validates token matches both in-memory state and on-disk lock file
        before releasing. This prevents accidentally deleting a lock that
        was recovered by another process after timeout.

        Args:
            token: LockToken from acquire().

        Raises:
            LockNotHeldError: If token doesn't match current lock or on-disk state.
        """
        if not self._validate_token(token):
            raise LockNotHeldError(
                f"Token does not match current lock for {self.dataset}"
            )

        try:
            # Verify on-disk ownership before unlinking to prevent deleting
            # a lock that was recovered by another process after timeout
            if self.lock_path.exists():
                content = self.lock_path.read_text()
                data = json.loads(content)
                if (
                    data.get("writer_id") != token.writer_id
                    or data.get("hostname") != token.hostname
                    or data.get("pid") != token.pid
                ):
                    self._current_token = None
                    raise LockNotHeldError(
                        f"Lock was recovered by another process for {self.dataset}"
                    )

            self.lock_path.unlink()
            # fsync parent directory for crash safety
            self._fsync_directory(self.lock_path.parent)
            self._current_token = None
            logger.info(
                "Lock released",
                extra={
                    "event": "sync.lock.released",
                    "dataset": self.dataset,
                    "writer_id": self.writer_id,
                },
            )
        except FileNotFoundError:
            # Lock already gone, treat as released
            self._current_token = None
        except (json.JSONDecodeError, OSError) as e:
            # Lock file corrupted or unreadable - don't delete it
            self._current_token = None
            logger.warning(
                "Cannot verify lock ownership, not releasing",
                extra={
                    "event": "sync.lock.release_failed",
                    "dataset": self.dataset,
                    "error": str(e),
                },
            )

    def refresh(self, token: LockToken) -> LockToken:
        """Refresh lock to extend expiry.

        Updates lock file with new expiry time. Validates on-disk ownership
        before writing to prevent clobbering a lock recovered by another process.

        Args:
            token: Current LockToken.

        Returns:
            New LockToken with extended expiry.

        Raises:
            LockNotHeldError: If token doesn't match current lock or on-disk state.
        """
        if not self._validate_token(token):
            raise LockNotHeldError(f"Cannot refresh - lock not held for {self.dataset}")

        # Verify on-disk ownership before writing to prevent clobbering
        # a lock that was recovered by another process after timeout
        if self.lock_path.exists():
            try:
                content = self.lock_path.read_text()
                data = json.loads(content)
                if (
                    data.get("writer_id") != token.writer_id
                    or data.get("hostname") != token.hostname
                    or data.get("pid") != token.pid
                ):
                    self._current_token = None
                    raise LockNotHeldError(
                        f"Cannot refresh - lock was recovered by another process for {self.dataset}"
                    )
            except (json.JSONDecodeError, OSError) as e:
                self._current_token = None
                raise LockNotHeldError(
                    f"Cannot refresh - lock file unreadable for {self.dataset}: {e}"
                ) from e
        else:
            self._current_token = None
            raise LockNotHeldError(
                f"Cannot refresh - lock file missing for {self.dataset}"
            )

        now = datetime.datetime.now(datetime.UTC)
        new_expires = now + datetime.timedelta(hours=self.LOCK_TIMEOUT_HOURS)

        new_token = LockToken(
            pid=token.pid,
            hostname=token.hostname,
            writer_id=token.writer_id,
            acquired_at=token.acquired_at,
            expires_at=new_expires,
            lock_path=token.lock_path,
        )

        # Update lock file atomically
        self._write_lock_file(new_token)
        self._current_token = new_token
        return new_token

    def _try_acquire(self) -> LockToken:
        """Attempt atomic lock creation.

        Uses O_CREAT|O_EXCL to atomically create lock file.

        Returns:
            LockToken on success.

        Raises:
            FileExistsError: If lock file already exists.
        """
        now = datetime.datetime.now(datetime.UTC)
        expires_at = now + datetime.timedelta(hours=self.LOCK_TIMEOUT_HOURS)

        token = LockToken(
            pid=os.getpid(),
            hostname=socket.gethostname(),
            writer_id=self.writer_id,
            acquired_at=now,
            expires_at=expires_at,
            lock_path=self.lock_path,
        )

        # Atomic creation with O_CREAT|O_EXCL
        fd = os.open(
            str(self.lock_path),
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o644,
        )
        try:
            os.write(fd, token.to_json().encode())
            os.fsync(fd)
        finally:
            os.close(fd)

        return token

    def _write_lock_file(self, token: LockToken) -> None:
        """Write lock file atomically with fsync.

        Uses temp file + rename pattern to prevent race conditions where
        another process could see a truncated/empty file during write.
        """
        temp_path = self.lock_path.with_suffix(f".lock.tmp.{os.getpid()}")
        try:
            with open(temp_path, "w") as f:
                f.write(token.to_json())
                f.flush()
                os.fsync(f.fileno())
            # Atomic replacement - prevents truncation race
            # Use Path.replace for cross-platform atomic overwrite (works on Windows too)
            temp_path.replace(self.lock_path)
            self._fsync_directory(self.lock_path.parent)
        except OSError:
            # Clean up temp file on failure
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
            raise

    def _is_lock_stale(self, lock_path: Path) -> tuple[bool, str]:
        """Check if lock file represents a stale lock.

        A lock is stale if:
        1. Lock file is malformed (invalid JSON, truncated, or extra fields)
        2. Lock has expired (past expires_at)
        3. Lock holder PID is dead (same hostname only)

        Note: Malformed locks are treated as stale to enable self-healing after
        crashes that may have left partial lock files.

        Args:
            lock_path: Path to lock file.

        Returns:
            Tuple of (is_stale, reason).
        """
        try:
            content = lock_path.read_text()
            data = json.loads(content)
        except (json.JSONDecodeError, OSError) as e:
            # Treat malformed/unreadable locks as stale for self-healing
            logger.warning(
                "Malformed lock file treated as stale",
                extra={
                    "event": "sync.lock.malformed",
                    "lock_path": str(lock_path),
                    "error": str(e),
                },
            )
            return True, f"Malformed lock file: {e}"

        # Validate schema - treat extra/missing fields as stale
        if set(data.keys()) != _LOCK_FIELDS:
            extra = set(data.keys()) - _LOCK_FIELDS
            missing = _LOCK_FIELDS - set(data.keys())
            reason = f"Invalid lock schema. Extra: {extra}, Missing: {missing}"
            logger.warning(
                "Lock with invalid schema treated as stale",
                extra={
                    "event": "sync.lock.malformed",
                    "lock_path": str(lock_path),
                    "reason": reason,
                },
            )
            return True, reason

        try:
            token = LockToken.from_dict(data, lock_path)
        except (KeyError, ValueError) as e:
            # Treat invalid token data as stale for self-healing
            logger.warning(
                "Lock with invalid data treated as stale",
                extra={
                    "event": "sync.lock.malformed",
                    "lock_path": str(lock_path),
                    "error": str(e),
                },
            )
            return True, f"Invalid lock data: {e}"

        # Check expiry
        if token.is_expired():
            return True, "Lock expired"

        # Check if holder is alive (only if same hostname)
        current_hostname = socket.gethostname()
        if token.hostname == current_hostname:
            if not self._is_pid_alive(token.pid):
                return True, f"Holder PID {token.pid} is dead"

        return False, ""

    def _is_pid_alive(self, pid: int) -> bool:
        """Check if process with given PID is running.

        Uses os.kill(pid, 0) which doesn't actually send a signal
        but checks if process exists.

        Args:
            pid: Process ID to check.

        Returns:
            True if process exists.
        """
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # Process exists but we can't signal it
            return True

    def _recover_stale_lock(self, lock_path: Path) -> bool:
        """Attempt to recover stale lock with deterministic winner selection.

        Uses atomic rename to claim recovery token. First process to
        successfully rename wins.

        Args:
            lock_path: Path to stale lock file.

        Returns:
            True if recovery succeeded and lock was deleted.
        """
        recovery_path = lock_path.with_suffix(f".lock.recovery.{os.getpid()}")

        try:
            # Atomic rename - first to succeed wins
            os.rename(str(lock_path), str(recovery_path))
            logger.info(
                "Won lock recovery",
                extra={
                    "event": "sync.lock.recovery_won",
                    "dataset": self.dataset,
                },
            )
            # We won - delete the recovery file
            recovery_path.unlink(missing_ok=True)
            # fsync parent directory to ensure recovery is durable
            self._fsync_directory(lock_path.parent)
            return True
        except FileNotFoundError:
            # Lock was already recovered by another process
            return False
        except OSError:
            # Another process won the rename race
            return False

    def _validate_token(self, token: LockToken) -> bool:
        """Validate token matches current lock state.

        Args:
            token: Token to validate.

        Returns:
            True if token is valid.
        """
        if self._current_token is None:
            return False

        return (
            token.pid == self._current_token.pid
            and token.hostname == self._current_token.hostname
            and token.writer_id == self._current_token.writer_id
            and token.lock_path == self._current_token.lock_path
        )

    def _fsync_directory(self, dir_path: Path) -> None:
        """Sync directory for crash safety.

        Ensures directory entries are persisted to disk.

        Args:
            dir_path: Directory to sync.
        """
        try:
            fd = os.open(str(dir_path), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            # Best effort - log warning but don't fail
            logger.warning(
                "Failed to fsync directory",
                extra={"path": str(dir_path)},
            )


@contextmanager
def atomic_lock(
    lock_dir: Path,
    dataset: str,
    writer_id: str | None = None,
    timeout_seconds: float = 30.0,
) -> Iterator[LockToken]:
    """Context manager for atomic file locking.

    Example:
        with atomic_lock(Path("data/locks"), "crsp") as token:
            # Exclusive access to crsp data
            sync_crsp_data()

    Args:
        lock_dir: Directory for lock files.
        dataset: Dataset name.
        writer_id: Unique writer identifier.
        timeout_seconds: Maximum wait for lock.

    Yields:
        LockToken proving lock ownership.

    Raises:
        LockAcquisitionError: If lock cannot be acquired.
    """
    lock = AtomicFileLock(lock_dir, dataset, writer_id)
    token = lock.acquire(timeout_seconds)
    try:
        yield token
    finally:
        lock.release(token)
