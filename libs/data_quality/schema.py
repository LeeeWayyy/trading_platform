"""
Schema registry for data quality framework.

This module provides:
- SchemaDrift: Result of schema drift detection
- DatasetSchema: Schema definition for a dataset
- SchemaRegistry: Manages expected schemas with versioning
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import re
import socket
import tempfile
import time
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from libs.data_quality.exceptions import LockNotHeldError, SchemaError

logger = logging.getLogger(__name__)


@dataclass
class SchemaDrift:
    """Result of schema drift detection.

    Attributes:
        added_columns: List of new columns not in expected schema.
        removed_columns: List of columns missing from current schema.
        changed_columns: List of (column, old_type, new_type) for type changes.
    """

    added_columns: list[str] = field(default_factory=list)
    removed_columns: list[str] = field(default_factory=list)
    changed_columns: list[tuple[str, str, str]] = field(default_factory=list)

    @property
    def is_breaking(self) -> bool:
        """True if drift includes removed or changed columns."""
        return bool(self.removed_columns or self.changed_columns)

    @property
    def has_additions(self) -> bool:
        """True if new columns were added."""
        return bool(self.added_columns)

    @property
    def has_drift(self) -> bool:
        """True if any drift was detected."""
        return self.is_breaking or self.has_additions


@dataclass
class DatasetSchema:
    """Schema definition for a dataset.

    Attributes:
        dataset: Dataset identifier.
        version: Schema version string (e.g., "v1.0.0").
        columns: Dict mapping column name to dtype string.
        created_at: UTC timestamp when schema was created.
        description: Optional description of the schema.
    """

    dataset: str
    version: str
    columns: dict[str, str]
    created_at: datetime
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "dataset": self.dataset,
            "version": self.version,
            "columns": self.columns,
            "created_at": self.created_at.isoformat(),
            "description": self.description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DatasetSchema:
        """Create from dictionary."""
        return cls(
            dataset=data["dataset"],
            version=data["version"],
            columns=data["columns"],
            created_at=datetime.fromisoformat(data["created_at"]),
            description=data.get("description", ""),
        )


class SchemaRegistry:
    """
    Manages expected schemas with versioning and drift detection.

    Storage: JSON files at data/schemas/{dataset}.json

    Version format: "v{major}.{minor}.{patch}"
    - major: Breaking changes (manual intervention required)
    - minor: Additive changes (auto-bumped on new columns)
    - patch: Metadata/description updates only

    Increment rules:
    - New columns detected → minor += 1, patch = 0
    - Description update only → patch += 1
    - Breaking drift → error, no version bump

    Locking: Uses file-level locking for concurrent writer safety
    - Acquire lock before read-modify-write
    - Use O_EXCL for atomic schema file creation
    - Release lock after fsync completes
    """

    SCHEMA_DIR = Path("data/schemas")
    LOCK_DIR = Path("data/locks")

    # Lock timeout constants
    LOCK_STALE_SECONDS = 300  # 5 minutes - check PID liveness after this
    LOCK_HARD_TIMEOUT_SECONDS = 1800  # 30 minutes - break any lock after this

    def __init__(
        self,
        storage_path: Path | None = None,
        lock_dir: Path | None = None,
    ) -> None:
        """Initialize schema registry.

        Args:
            storage_path: Directory for schema files.
            lock_dir: Directory for lock files.
        """
        self.storage_path = storage_path or self.SCHEMA_DIR
        self.lock_dir = lock_dir or self.LOCK_DIR

        self.storage_path.mkdir(parents=True, exist_ok=True)
        self.lock_dir.mkdir(parents=True, exist_ok=True)

    def _sanitize_dataset(self, dataset: str) -> str:
        """Sanitize dataset name to prevent path traversal.

        Args:
            dataset: Raw dataset identifier.

        Returns:
            Sanitized dataset name (basename only, no path components).

        Raises:
            ValueError: If dataset name is empty after sanitization.
        """
        sanitized = Path(dataset).name
        if not sanitized or sanitized in (".", ".."):
            raise ValueError(f"Invalid dataset name: {dataset!r}")
        return sanitized

    def _schema_path(self, dataset: str) -> Path:
        """Get schema file path for dataset."""
        return self.storage_path / f"{self._sanitize_dataset(dataset)}.json"

    def _lock_path(self, dataset: str) -> Path:
        """Get lock file path for dataset schema."""
        return self.lock_dir / f"schema_{self._sanitize_dataset(dataset)}.lock"

    @contextmanager
    def _acquire_lock(
        self,
        dataset: str,
        timeout_seconds: float = 30.0,
        retry_interval: float = 0.1,
    ) -> Generator[Path, None, None]:
        """Acquire exclusive lock for schema operations.

        Uses O_CREAT | O_EXCL for atomic lock file creation.

        Args:
            dataset: Dataset to lock.
            timeout_seconds: Max time to wait for lock.
            retry_interval: Time between retry attempts.

        Yields:
            Path to lock file.

        Raises:
            LockNotHeldError: If lock cannot be acquired within timeout.
        """
        lock_path = self._lock_path(dataset)
        lock_data = {
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "acquired_at": datetime.now(UTC).isoformat(),
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
                try:
                    mtime = lock_path.stat().st_mtime
                    age_seconds = time.time() - mtime

                    # Hard timeout: break ANY lock older than 30 minutes
                    # This prevents permanent deadlock from crashed remote workers
                    if age_seconds > self.LOCK_HARD_TIMEOUT_SECONDS:
                        logger.warning(
                            "Breaking stale schema lock for %s "
                            "(%.1f seconds old, exceeds hard timeout of %d seconds)",
                            dataset,
                            age_seconds,
                            self.LOCK_HARD_TIMEOUT_SECONDS,
                        )
                        lock_path.unlink(missing_ok=True)
                    # Soft timeout: check PID liveness after 5 minutes
                    elif age_seconds > self.LOCK_STALE_SECONDS:
                        # Read lock file to get PID and hostname
                        try:
                            with open(lock_path) as f:
                                existing_lock = json.load(f)
                            owner_pid = existing_lock.get("pid")
                            owner_hostname = existing_lock.get("hostname")
                            current_hostname = socket.gethostname()

                            # Only check PID if lock is from same host
                            # Remote locks wait for hard timeout (can't check PID)
                            if owner_hostname == current_hostname:
                                process_alive = False
                                if owner_pid:
                                    try:
                                        # Signal 0 checks if process exists (doesn't send signal)
                                        # KNOWN LIMITATION: PID reuse risk
                                        # If the original process crashes and its PID is recycled
                                        # by a new (unrelated) process before lock recovery runs,
                                        # this check will incorrectly think the owner is still alive.
                                        # Mitigation: Hard timeout (30 min) provides upper bound.
                                        # Alternative: Use psutil.Process(pid).create_time() to
                                        # verify process identity, but adds external dependency.
                                        os.kill(owner_pid, 0)
                                        process_alive = True
                                    except (OSError, ProcessLookupError):
                                        process_alive = False

                                if not process_alive:
                                    logger.warning(
                                        "Removing stale schema lock for %s "
                                        "(%.1f seconds old, owner PID %s not alive)",
                                        dataset,
                                        age_seconds,
                                        owner_pid,
                                    )
                                    lock_path.unlink(missing_ok=True)
                                else:
                                    logger.debug(
                                        "Lock for %s is old but owner PID %s still alive",
                                        dataset,
                                        owner_pid,
                                    )
                            else:
                                # Remote lock - wait for hard timeout
                                logger.debug(
                                    "Lock for %s is from remote host %s "
                                    "(local: %s), waiting for hard timeout",
                                    dataset,
                                    owner_hostname,
                                    current_hostname,
                                )
                        except (json.JSONDecodeError, OSError):
                            # Can't read lock file, remove it
                            logger.warning(
                                "Removing unreadable schema lock for %s",
                                dataset,
                            )
                            lock_path.unlink(missing_ok=True)
                except OSError:
                    pass
                time.sleep(retry_interval)

        if not acquired:
            raise LockNotHeldError(
                f"Failed to acquire schema lock for {dataset} within {timeout_seconds}s"
            )

        try:
            yield lock_path
        finally:
            # Release lock - use fcntl.flock for atomic read-verify-delete
            # This prevents TOCTOU race between reading and deleting
            try:
                if lock_path.exists():
                    fd = os.open(lock_path, os.O_RDONLY)
                    try:
                        # Acquire exclusive lock to prevent concurrent modification
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        try:
                            # Read and verify ownership while holding flock
                            with os.fdopen(os.dup(fd), "r") as f:
                                current_lock = json.load(f)

                            # Only delete if we still own the lock
                            if (
                                current_lock.get("pid") == lock_data["pid"]
                                and current_lock.get("hostname") == lock_data["hostname"]
                            ):
                                lock_path.unlink(missing_ok=True)
                            else:
                                logger.warning(
                                    "Lock for %s was taken over by another process "
                                    "(pid=%s, hostname=%s), not releasing",
                                    dataset,
                                    current_lock.get("pid"),
                                    current_lock.get("hostname"),
                                )
                        finally:
                            fcntl.flock(fd, fcntl.LOCK_UN)
                    except BlockingIOError:
                        # Another process holds the flock - don't delete
                        logger.debug(
                            "Lock for %s is held by another process, not releasing",
                            dataset,
                        )
                    finally:
                        os.close(fd)
            except (json.JSONDecodeError, OSError) as e:
                # Lock file unreadable or gone - safe to ignore
                logger.warning("Failed to release schema lock: %s", e)

    def get_expected_schema(
        self,
        dataset: str,
        version: str | None = None,
    ) -> DatasetSchema | None:
        """Get expected schema for dataset.

        Args:
            dataset: Dataset identifier.
            version: Specific version to retrieve (None for latest).

        Returns:
            DatasetSchema if found, None otherwise.
        """
        path = self._schema_path(dataset)
        if not path.exists():
            return None

        with open(path) as f:
            data = json.load(f)

        # If specific version requested, check history
        if version and data.get("version") != version:
            history = data.get("history", [])
            for entry in history:
                if entry.get("version") == version:
                    return DatasetSchema.from_dict(entry)
            return None

        return DatasetSchema.from_dict(data)

    def detect_drift(
        self,
        dataset: str,
        current_schema: dict[str, str],
    ) -> SchemaDrift:
        """Detect schema drift from expected.

        Policy:
        - New columns: Accept with warning, auto-version bump
        - Removed columns: Reject, block manifest update
        - Type changes: Reject, block manifest update

        Args:
            dataset: Dataset identifier.
            current_schema: Current schema from data (column -> dtype).

        Returns:
            SchemaDrift describing detected changes.
        """
        expected = self.get_expected_schema(dataset)

        if expected is None:
            # No expected schema - all columns are "new"
            return SchemaDrift(added_columns=list(current_schema.keys()))

        expected_cols = expected.columns
        current_cols = current_schema

        # Find added columns
        added = [col for col in current_cols if col not in expected_cols]

        # Find removed columns
        removed = [col for col in expected_cols if col not in current_cols]

        # Find type changes
        changed = []
        for col in current_cols:
            if col in expected_cols:
                old_type = expected_cols[col].lower()
                new_type = current_cols[col].lower()
                if old_type != new_type:
                    changed.append((col, expected_cols[col], current_cols[col]))

        return SchemaDrift(
            added_columns=added,
            removed_columns=removed,
            changed_columns=changed,
        )

    def register_schema(
        self,
        dataset: str,
        schema: dict[str, str],
        description: str = "",
    ) -> str:
        """Register new schema version.

        Uses atomic temp+fsync write with lock.

        Args:
            dataset: Dataset identifier.
            schema: Schema to register (column -> dtype).
            description: Optional description.

        Returns:
            New version string.
        """
        with self._acquire_lock(dataset):
            current = self.get_expected_schema(dataset)

            if current is None:
                version = "v1.0.0"
            else:
                version = self._increment_version(current.version, "minor")

            new_schema = DatasetSchema(
                dataset=dataset,
                version=version,
                columns=schema,
                created_at=datetime.now(UTC),
                description=description,
            )

            # Prepare data with history
            data = new_schema.to_dict()
            if current:
                path = self._schema_path(dataset)
                with open(path) as f:
                    old_data = json.load(f)
                history = old_data.get("history", [])
                # Add current to history (include dataset for from_dict compatibility)
                history.append(
                    {
                        "dataset": current.dataset,
                        "version": current.version,
                        "columns": current.columns,
                        "created_at": current.created_at.isoformat(),
                        "description": current.description,
                    }
                )
                data["history"] = history

            self._atomic_write(self._schema_path(dataset), data)

            logger.info("Registered schema %s version %s", dataset, version)
            return version

    def apply_drift_policy(
        self,
        dataset: str,
        drift: SchemaDrift,
        current_schema: dict[str, str],
    ) -> tuple[str, str]:
        """Apply drift policy and handle auto-version bump.

        If breaking drift (removed/changed columns):
            - Raises SchemaError
            - Blocks manifest update

        If additions only:
            1. Logs WARNING about new columns
            2. Creates new schema version (atomic write + fsync)
            3. Persists updated schema to registry
            4. Returns (new_version, message)

        Args:
            dataset: Dataset identifier.
            drift: Detected schema drift.
            current_schema: Current schema from data.

        Returns:
            (new_schema_version, message) tuple.

        Raises:
            SchemaError: If drift is breaking (removed/changed).
        """
        if drift.is_breaking:
            details = []
            if drift.removed_columns:
                details.append(f"Removed columns: {drift.removed_columns}")
            if drift.changed_columns:
                changes = [f"{col}: {old} -> {new}" for col, old, new in drift.changed_columns]
                details.append(f"Changed types: {changes}")

            raise SchemaError(
                drift, f"Breaking schema drift detected for {dataset}: {'; '.join(details)}"
            )

        if not drift.has_additions:
            # No drift at all
            current = self.get_expected_schema(dataset)
            if current:
                return current.version, "No schema changes detected"
            # First sync - register initial schema
            version = self.register_schema(dataset, current_schema)
            return version, f"Initial schema registered as {version}"

        # Additive drift - auto-version bump with lock
        logger.warning(
            "Schema drift detected for %s: new columns %s",
            dataset,
            drift.added_columns,
        )

        with self._acquire_lock(dataset):
            # Get current version and bump minor (re-read under lock)
            current = self.get_expected_schema(dataset)
            if current:
                new_version = self._increment_version(current.version, "minor")
            else:
                new_version = "v1.0.0"

            # Create new schema entry
            new_schema = DatasetSchema(
                dataset=dataset,
                version=new_version,
                columns=current_schema,
                created_at=datetime.now(UTC),
                description=f"Auto-bumped: added columns {drift.added_columns}",
            )

            # Prepare data with history
            data = new_schema.to_dict()
            if current:
                path = self._schema_path(dataset)
                with open(path) as f:
                    old_data = json.load(f)
                history = old_data.get("history", [])
                # Include dataset for from_dict compatibility
                history.append(
                    {
                        "dataset": current.dataset,
                        "version": current.version,
                        "columns": current.columns,
                        "created_at": current.created_at.isoformat(),
                        "description": current.description,
                    }
                )
                data["history"] = history

            self._atomic_write(self._schema_path(dataset), data)

            message = (
                f"Auto-bumped schema from {current.version if current else 'N/A'} "
                f"to {new_version}: added columns {drift.added_columns}"
            )
            logger.info(message)

        return new_version, message

    def _increment_version(
        self,
        version: str,
        level: Literal["major", "minor", "patch"],
    ) -> str:
        """Increment version string.

        Args:
            version: Current version (e.g., "v1.2.3").
            level: Which part to increment.

        Returns:
            New version string.
        """
        match = re.match(r"v(\d+)\.(\d+)\.(\d+)", version)
        if not match:
            return "v1.0.0"

        major, minor, patch = int(match.group(1)), int(match.group(2)), int(match.group(3))

        if level == "major":
            return f"v{major + 1}.0.0"
        elif level == "minor":
            return f"v{major}.{minor + 1}.0"
        else:  # patch
            return f"v{major}.{minor}.{patch + 1}"

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
