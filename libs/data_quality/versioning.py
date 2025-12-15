"""
Dataset versioning for reproducible research.

This module provides:
- SnapshotManifest: Metadata for dataset snapshots
- DatasetSnapshot: Per-dataset snapshot info with per-file storage metadata
- DatasetVersionManager: Manages snapshots, time-travel, and backtest linkage
- CAS (Content-Addressable Storage): Reference-counted file deduplication

Key features:
- Git-like versioning with version tags
- Time-travel queries (query_as_of)
- Backtest linkage for reproducibility
- Hardlink/copy/CAS storage with automatic fallback
- Atomic snapshot creation with staging directory pattern
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, Field, PlainSerializer, field_validator

from libs.data_quality.exceptions import (
    DataNotFoundError,
    DatasetNotInSnapshotError,
    LockNotHeldError,
    SnapshotInconsistentError,
    SnapshotNotFoundError,
    SnapshotReferencedError,
)
from libs.data_quality.manifest import ManifestManager
from libs.data_quality.validation import DataValidator

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


# =============================================================================
# Data Models
# =============================================================================


class FileStorageInfo(BaseModel):
    """Per-file storage metadata."""

    path: str  # Relative path within snapshot
    original_path: str  # Original source path
    storage_mode: Literal["hardlink", "copy", "cas"]
    target: str  # Hardlink/copy path OR CAS hash
    size_bytes: int
    checksum: str  # SHA-256 of file content

    model_config = {"frozen": True}


class DatasetSnapshot(BaseModel):
    """Snapshot info for a single dataset."""

    dataset: str
    sync_manifest_version: int
    files: list[FileStorageInfo]
    row_count: int
    date_range_start: date
    date_range_end: date

    model_config = {"frozen": True}

    @property
    def date_range(self) -> tuple[date, date]:
        """Get date range as tuple."""
        return (self.date_range_start, self.date_range_end)


class SnapshotManifest(BaseModel):
    """Metadata for a dataset snapshot."""

    version_tag: str
    created_at: datetime
    datasets: dict[str, DatasetSnapshot]
    total_size_bytes: int
    aggregate_checksum: str
    referenced_by: list[str] = Field(default_factory=list)
    prev_snapshot_checksum: str | None = None

    model_config = {"frozen": False}

    @field_validator("created_at")
    @classmethod
    def validate_utc(cls, v: datetime) -> datetime:
        """Ensure timestamp is UTC."""
        if v.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        offset = v.utcoffset()
        if offset is None or offset.total_seconds() != 0:
            raise ValueError("created_at must be UTC")
        return v


class BacktestLinkage(BaseModel):
    """Durable backtest -> snapshot mapping."""

    backtest_id: str
    created_at: datetime
    snapshot_version: str
    dataset_versions: dict[str, int]  # dataset -> manifest_version
    checksum: str
    orphaned_at: datetime | None = None  # Set when snapshot is deleted

    model_config = {"frozen": False}


class CASEntry(BaseModel):
    """CAS reference counting entry."""

    hash: str
    size_bytes: int
    original_path: str
    created_at: datetime
    ref_count: int
    referencing_snapshots: list[str]

    model_config = {"frozen": False}


class CASIndex(BaseModel):
    """CAS reference counting index."""

    files: dict[str, CASEntry] = Field(default_factory=dict)
    total_size_bytes: int = 0
    last_gc_at: datetime | None = None

    model_config = {"frozen": False}


def _decode_base64_bytes(v: Any) -> bytes | None:
    """Decode base64 string to bytes, pass through bytes/None."""
    if v is None:
        return None
    if isinstance(v, bytes):
        return v
    if isinstance(v, str):
        return base64.b64decode(v)
    raise ValueError(f"Expected bytes, str, or None, got {type(v)}")


def _encode_bytes_to_base64(v: bytes | None) -> str | None:
    """Encode bytes to base64 string for JSON serialization."""
    if v is None:
        return None
    return base64.b64encode(v).decode("ascii")


# Type annotation for bytes fields that serialize to/from base64
Base64Bytes = Annotated[
    bytes | None,
    BeforeValidator(_decode_base64_bytes),
    PlainSerializer(_encode_bytes_to_base64, return_type=str | None),
]


class DiffFileEntry(BaseModel):
    """File entry in a diff."""

    path: str
    old_hash: str | None  # None for added files
    new_hash: str
    storage: Literal["inline", "cas"]
    inline_data: Base64Bytes = None  # Serialized as base64 for JSON safety
    cas_hash: str | None = None

    model_config = {"frozen": True}


class SnapshotDiff(BaseModel):
    """Compressed diff between snapshots."""

    from_version: str
    to_version: str
    created_at: datetime
    added_files: list[DiffFileEntry]
    removed_files: list[str]
    changed_files: list[DiffFileEntry]
    checksum: str
    orphaned_at: datetime | None = None

    model_config = {"frozen": False}


# =============================================================================
# Dataset Version Manager
# =============================================================================


class DatasetVersionManager:
    """Manages dataset versions for reproducibility.

    Provides:
    - Snapshot creation with hardlink/copy/CAS storage
    - Time-travel queries (query_as_of)
    - Backtest linkage for reproducibility
    - Retention policy enforcement
    - Snapshot integrity verification
    """

    # Directory structure
    SNAPSHOTS_DIR = Path("data/snapshots")
    CAS_DIR = Path("data/cas")
    DIFFS_DIR = Path("data/diffs")
    BACKTESTS_DIR = Path("data/backtests")
    LOCKS_DIR = Path("data/locks")

    # Retention policy
    RETENTION_DAYS = 90
    DIFF_MAX_STORAGE_BYTES = 10 * 1024**3  # 10 GB
    DIFF_MAX_SINGLE_SIZE_BYTES = 500 * 1024**2  # 500 MB
    DIFF_CLEANUP_AFTER_DAYS = 30

    # Lock timeouts
    SNAPSHOT_LOCK_TIMEOUT_SECONDS = 30.0

    # Version tag patterns
    DATE_TAG_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    # Safe version tag pattern: alphanumeric, dots, hyphens, underscores
    SAFE_TAG_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

    # Default data root for file path validation
    DATA_ROOT = Path("data")

    def __init__(
        self,
        manifest_manager: ManifestManager,
        validator: DataValidator | None = None,
        snapshots_dir: Path | None = None,
        cas_dir: Path | None = None,
        diffs_dir: Path | None = None,
        backtests_dir: Path | None = None,
        locks_dir: Path | None = None,
        data_root: Path | None = None,
    ) -> None:
        """Initialize version manager.

        Args:
            manifest_manager: Manager for sync manifests.
            validator: Data validator for checksums (creates default if None).
            snapshots_dir: Directory for snapshots.
            cas_dir: Directory for CAS storage.
            diffs_dir: Directory for diffs.
            backtests_dir: Directory for backtest linkages.
            locks_dir: Directory for lock files.
            data_root: Root directory for data files (security boundary).
        """
        self.manifest_manager = manifest_manager
        self.validator = validator or DataValidator()

        self.snapshots_dir = snapshots_dir or self.SNAPSHOTS_DIR
        self.cas_dir = cas_dir or self.CAS_DIR
        self.diffs_dir = diffs_dir or self.DIFFS_DIR
        self.backtests_dir = backtests_dir or self.BACKTESTS_DIR
        self.locks_dir = locks_dir or self.LOCKS_DIR
        self.data_root = data_root or self.DATA_ROOT

        # Ensure directories exist
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.cas_dir.mkdir(parents=True, exist_ok=True)
        self.diffs_dir.mkdir(parents=True, exist_ok=True)
        self.backtests_dir.mkdir(parents=True, exist_ok=True)
        self.locks_dir.mkdir(parents=True, exist_ok=True)

    def _validate_identifier(
        self,
        identifier: str,
        base_dir: Path,
        identifier_type: str = "identifier",
    ) -> None:
        """Validate an identifier for path safety.

        Validates that:
        1. Identifier matches SAFE_TAG_PATTERN
        2. Resolved path stays within base_dir (no path traversal)

        Args:
            identifier: The identifier to validate (version_tag, backtest_id, etc.)
            base_dir: Base directory the identifier will be used with.
            identifier_type: Type name for error messages.

        Raises:
            ValueError: If identifier is invalid or enables path traversal.
        """
        if not identifier or not self.SAFE_TAG_PATTERN.match(identifier):
            raise ValueError(
                f"Invalid {identifier_type}: {identifier}. "
                "Must match pattern: [A-Za-z0-9][A-Za-z0-9._-]*"
            )

        # Verify resolved path stays within base_dir
        test_path = base_dir / identifier
        resolved_path = test_path.resolve()
        if not resolved_path.is_relative_to(base_dir.resolve()):
            raise ValueError(f"Invalid {identifier_type} (path traversal detected): {identifier}")

    def _validate_file_path(self, file_path: Path) -> None:
        """Validate a file path for security.

        Validates that:
        1. Resolved path is within data_root (prevents path traversal via manifest)
        2. Path is not a symlink (prevents leaking arbitrary files)

        Args:
            file_path: File path to validate.

        Raises:
            ValueError: If file path is outside data_root or is a symlink.
        """
        resolved = file_path.resolve()

        # Reject symlinks - they could point anywhere
        if file_path.is_symlink():
            raise ValueError(f"Symlinks not allowed in manifest: {file_path}")

        # Verify file is within data_root
        data_root_resolved = self.data_root.resolve()
        if not resolved.is_relative_to(data_root_resolved):
            raise ValueError(
                f"File path outside data root: {file_path}. " f"Must be within: {self.data_root}"
            )

    # =========================================================================
    # Snapshot Creation
    # =========================================================================

    def create_snapshot(
        self,
        version_tag: str,
        datasets: list[str] | None = None,
        use_cas: bool = True,
    ) -> SnapshotManifest:
        """Create immutable snapshot of current dataset states.

        Uses optimistic concurrency control: pins manifest versions at start,
        verifies they haven't changed at end.

        Args:
            version_tag: Unique identifier for this snapshot.
            datasets: List of datasets to include (None = all available).
            use_cas: Whether to use content-addressable storage.

        Returns:
            SnapshotManifest for the created snapshot.

        Raises:
            ValueError: If version_tag already exists or is invalid.
            SnapshotInconsistentError: If dataset was modified during snapshot.
        """
        # Validate version tag - prevent path traversal
        if not version_tag or not self.SAFE_TAG_PATTERN.match(version_tag):
            raise ValueError(
                f"Invalid version tag: {version_tag}. "
                "Must match pattern: [A-Za-z0-9][A-Za-z0-9._-]*"
            )

        snapshot_path = self.snapshots_dir / version_tag

        # Additional path traversal protection - verify resolved path
        resolved_path = snapshot_path.resolve()
        if not resolved_path.is_relative_to(self.snapshots_dir.resolve()):
            raise ValueError(f"Invalid version tag (path traversal detected): {version_tag}")

        if snapshot_path.exists():
            raise ValueError(f"Snapshot already exists: {version_tag}")

        # Get datasets to snapshot
        if datasets is None:
            available_manifests = self.manifest_manager.list_manifests()
            datasets = [m.dataset for m in available_manifests]

        if not datasets:
            raise ValueError("No datasets to snapshot")

        # Pin manifest versions (optimistic concurrency)
        pinned_versions: dict[str, int] = {}
        sync_manifests = {}
        for ds in datasets:
            manifest = self.manifest_manager.load_manifest(ds)
            if manifest is None:
                raise DataNotFoundError(f"No manifest found for dataset: {ds}")
            pinned_versions[ds] = manifest.manifest_version
            sync_manifests[ds] = manifest

        # Create staging directory
        staging_path = self.snapshots_dir / f".staging_{version_tag}_{os.getpid()}"

        # Track CAS hashes added during this snapshot for cleanup on failure
        cas_hashes_added: list[str] = []
        # Track new CAS files (not previously in index) for cleanup
        cas_new_files: list[str] = []
        # Track whether CAS index was persisted
        cas_index_persisted = False

        # Acquire lock for entire snapshot operation (protects CAS index)
        with self._acquire_snapshot_lock():
            try:
                staging_path.mkdir(parents=True, exist_ok=True)
                files_dir = staging_path / "files"
                files_dir.mkdir(parents=True, exist_ok=True)

                # Load CAS index once at start (batch optimization)
                cas_index = self._load_cas_index() if use_cas else None
                # Track existing hashes for new file detection
                existing_cas_hashes = set(cas_index.files.keys()) if cas_index else set()

                dataset_snapshots: dict[str, DatasetSnapshot] = {}
                total_size = 0
                all_files: list[FileStorageInfo] = []

                # Process each dataset
                for ds in datasets:
                    sync_manifest = sync_manifests[ds]
                    ds_files: list[FileStorageInfo] = []

                    for file_path_str in sync_manifest.file_paths:
                        file_path = Path(file_path_str)
                        if not file_path.exists():
                            raise DataNotFoundError(f"File not found: {file_path}")

                        # Security: validate file path is within data root and not a symlink
                        self._validate_file_path(file_path)

                        # Compute checksum
                        checksum = self.validator.compute_checksum(file_path)
                        file_size = file_path.stat().st_size
                        total_size += file_size

                        # Determine storage mode and create file entry
                        storage_info = self._store_file_batched(
                            file_path=file_path,
                            checksum=checksum,
                            dest_dir=files_dir,
                            version_tag=version_tag,
                            use_cas=use_cas,
                            cas_index=cas_index,
                            cas_hashes_added=cas_hashes_added,
                            existing_cas_hashes=existing_cas_hashes,
                            cas_new_files=cas_new_files,
                        )

                        ds_files.append(storage_info)

                    # Create dataset snapshot
                    ds_snapshot = DatasetSnapshot(
                        dataset=ds,
                        sync_manifest_version=sync_manifest.manifest_version,
                        files=ds_files,
                        row_count=sync_manifest.row_count,
                        date_range_start=sync_manifest.start_date,
                        date_range_end=sync_manifest.end_date,
                    )
                    dataset_snapshots[ds] = ds_snapshot
                    all_files.extend(ds_files)

                # Verify manifest versions haven't changed
                for ds, pinned_v in pinned_versions.items():
                    current = self.manifest_manager.load_manifest(ds)
                    if current is None or current.manifest_version != pinned_v:
                        actual_v = current.manifest_version if current else -1
                        raise SnapshotInconsistentError(ds, pinned_v, actual_v)

                # Save CAS index once after all files processed (batch optimization)
                if cas_index is not None:
                    self._save_cas_index(cas_index)
                    cas_index_persisted = True

                # Compute aggregate checksum
                aggregate_checksum = self._compute_aggregate_checksum(all_files)

                # Get previous snapshot for hash chain
                prev_checksum = self._get_latest_snapshot_checksum()

                # Create snapshot manifest
                now = datetime.now(UTC)
                snapshot = SnapshotManifest(
                    version_tag=version_tag,
                    created_at=now,
                    datasets=dataset_snapshots,
                    total_size_bytes=total_size,
                    aggregate_checksum=aggregate_checksum,
                    referenced_by=[],
                    prev_snapshot_checksum=prev_checksum,
                )

                # Write manifest to staging
                manifest_path = staging_path / "manifest.json"
                self._atomic_write_json(manifest_path, snapshot.model_dump(mode="json"))

                # Atomic commit: rename staging to final
                staging_path.rename(snapshot_path)

                # Fsync parent directory
                self._fsync_directory(self.snapshots_dir)

                logger.info(
                    "Created snapshot",
                    extra={
                        "version_tag": version_tag,
                        "datasets": list(dataset_snapshots.keys()),
                        "total_size_bytes": total_size,
                    },
                )

                return snapshot

            except Exception:
                # Cleanup on failure - release CAS refs we added
                self._cleanup_partial_snapshot_with_cas(
                    staging_path,
                    version_tag,
                    cas_hashes_added,
                    cas_new_files,
                    cas_index_persisted,
                )
                raise

    def _store_file_batched(
        self,
        file_path: Path,
        checksum: str,
        dest_dir: Path,
        version_tag: str,
        use_cas: bool,
        cas_index: CASIndex | None,
        cas_hashes_added: list[str],
        existing_cas_hashes: set[str],
        cas_new_files: list[str],
    ) -> FileStorageInfo:
        """Store a file using appropriate storage mode with batched CAS.

        Priority: CAS > copy (NO hardlinks - they break immutability)

        Hardlinks are NOT used because they share the same inode as the source.
        If the source file is later modified, the snapshot would also change,
        breaking the immutability guarantee required for reproducible backtests.

        Args:
            file_path: Source file path.
            checksum: File checksum.
            dest_dir: Destination directory for copies.
            version_tag: Snapshot version tag.
            use_cas: Whether CAS is enabled.
            cas_index: In-memory CAS index (mutated in place).
            cas_hashes_added: List to track CAS hashes added (for cleanup).
            existing_cas_hashes: Set of CAS hashes that existed before this snapshot.
            cas_new_files: List to track NEW CAS files created (not in existing).
        """
        # Generate unique filename within snapshot
        file_hash_prefix = checksum[:8]
        dest_name = f"{file_hash_prefix}_{file_path.name}"
        dest_path = dest_dir / dest_name

        storage_mode: Literal["hardlink", "copy", "cas"]
        target: str

        # Use CAS if enabled (provides deduplication while preserving immutability)
        if use_cas and cas_index is not None:
            try:
                cas_hash = self._store_in_cas_batched(
                    file_path,
                    checksum,
                    version_tag,
                    cas_index,
                    cas_hashes_added,
                    existing_cas_hashes,
                    cas_new_files,
                )
                storage_mode = "cas"
                target = cas_hash
            except OSError as e:
                # CAS failed (disk full, permissions, etc) - fall back to copy
                logger.warning("CAS storage failed, falling back to copy: %s", e)
                self._copy_with_fsync(file_path, dest_path, checksum)
                storage_mode = "copy"
                target = str(dest_path)
        else:
            # Copy creates an independent file (immutable snapshot)
            self._copy_with_fsync(file_path, dest_path, checksum)
            storage_mode = "copy"
            target = str(dest_path)
            logger.debug("Copied file: %s -> %s", file_path, dest_path)

        return FileStorageInfo(
            path=dest_name,
            original_path=str(file_path),
            storage_mode=storage_mode,
            target=target,
            size_bytes=file_path.stat().st_size,
            checksum=checksum,
        )

    def _store_file(
        self,
        file_path: Path,
        checksum: str,
        dest_dir: Path,
        version_tag: str,
        use_cas: bool,
    ) -> FileStorageInfo:
        """Store a file using appropriate storage mode (legacy non-batched).

        Priority: CAS > copy (NO hardlinks - they break immutability)
        """
        # Generate unique filename within snapshot
        file_hash_prefix = checksum[:8]
        dest_name = f"{file_hash_prefix}_{file_path.name}"
        dest_path = dest_dir / dest_name

        storage_mode: Literal["hardlink", "copy", "cas"]
        target: str

        # Use CAS if enabled (provides deduplication while preserving immutability)
        if use_cas:
            try:
                cas_hash = self._store_in_cas(file_path, checksum, version_tag)
                storage_mode = "cas"
                target = cas_hash
            except OSError as e:
                # CAS failed - fall back to copy
                logger.warning("CAS storage failed, falling back to copy: %s", e)
                self._copy_with_fsync(file_path, dest_path, checksum)
                storage_mode = "copy"
                target = str(dest_path)
        else:
            # Copy creates an independent file (immutable snapshot)
            self._copy_with_fsync(file_path, dest_path, checksum)
            storage_mode = "copy"
            target = str(dest_path)
            logger.debug("Copied file: %s -> %s", file_path, dest_path)

        return FileStorageInfo(
            path=dest_name,
            original_path=str(file_path),
            storage_mode=storage_mode,
            target=target,
            size_bytes=file_path.stat().st_size,
            checksum=checksum,
        )

    def _cleanup_partial_snapshot(self, staging_path: Path, version_tag: str) -> None:
        """Clean up after failed snapshot creation (legacy)."""
        if not staging_path.exists():
            return

        # Release CAS refs if manifest exists
        manifest_path = staging_path / "manifest.json"
        if manifest_path.exists():
            try:
                with open(manifest_path) as f:
                    data = json.load(f)
                snapshot = SnapshotManifest.model_validate(data)
                for ds in snapshot.datasets.values():
                    for file_info in ds.files:
                        if file_info.storage_mode == "cas":
                            self._release_cas_ref(file_info.target, version_tag)
            except Exception as e:
                logger.warning("Failed to release CAS refs during cleanup: %s", e)

        # Remove staging directory
        try:
            shutil.rmtree(staging_path, ignore_errors=True)
        except Exception as e:
            logger.warning("Failed to remove staging directory: %s", e)

    def _cleanup_partial_snapshot_with_cas(
        self,
        staging_path: Path,
        version_tag: str,
        cas_hashes_added: list[str],
        cas_new_files: list[str],
        cas_index_persisted: bool,
    ) -> None:
        """Clean up after failed snapshot creation with tracked CAS hashes.

        Handles two cases:
        1. If CAS index was persisted: decrement refs (they were actually incremented)
        2. If NOT persisted: delete NEW CAS files from disk (refs weren't persisted,
           so we only need to remove the physical files we created)

        Args:
            staging_path: Staging directory to remove.
            version_tag: Snapshot being cleaned up.
            cas_hashes_added: All CAS hashes we touched (for persisted case).
            cas_new_files: NEW CAS files we created (for unpersisted case).
            cas_index_persisted: Whether CAS index was saved to disk.
        """
        if cas_index_persisted:
            # Index was persisted - decrement refs (they exist on disk)
            if cas_hashes_added:
                try:
                    cas_index = self._load_cas_index()
                    for cas_hash in cas_hashes_added:
                        if cas_hash in cas_index.files:
                            entry = cas_index.files[cas_hash]
                            entry.ref_count -= 1
                            if version_tag in entry.referencing_snapshots:
                                entry.referencing_snapshots.remove(version_tag)
                    self._save_cas_index(cas_index)
                    logger.debug(
                        "Released %d CAS refs during cleanup (persisted)",
                        len(cas_hashes_added),
                    )
                except Exception as e:
                    logger.warning("Failed to release CAS refs during cleanup: %s", e)
        else:
            # Index was NOT persisted - only delete NEW physical files
            # Don't touch the index (our increments don't exist on disk)
            if cas_new_files:
                deleted_count = 0
                for cas_hash in cas_new_files:
                    # CAS files are stored without extension (checksum only)
                    cas_path = self._get_cas_path(cas_hash)
                    if cas_path.exists():
                        try:
                            cas_path.unlink()
                            deleted_count += 1
                        except Exception as e:
                            logger.warning(
                                "Failed to delete CAS file %s during cleanup: %s",
                                cas_path,
                                e,
                            )
                logger.debug(
                    "Deleted %d new CAS files during cleanup (unpersisted)",
                    deleted_count,
                )

        # Remove staging directory
        if staging_path.exists():
            try:
                shutil.rmtree(staging_path, ignore_errors=True)
            except Exception as e:
                logger.warning("Failed to remove staging directory: %s", e)

    # =========================================================================
    # Snapshot Retrieval
    # =========================================================================

    def get_snapshot(self, version_tag: str) -> SnapshotManifest | None:
        """Retrieve snapshot metadata by version tag.

        Args:
            version_tag: Snapshot version tag.

        Returns:
            SnapshotManifest or None if not found.

        Raises:
            ValueError: If version_tag is invalid (path traversal attempt).
        """
        # Validate version tag to prevent path traversal
        self._validate_identifier(version_tag, self.snapshots_dir, "version_tag")

        snapshot_path = self.snapshots_dir / version_tag / "manifest.json"
        if not snapshot_path.exists():
            return None

        with open(snapshot_path) as f:
            data = json.load(f)

        return SnapshotManifest.model_validate(data)

    def get_data_at_version(self, dataset: str, version_tag: str) -> Path:
        """Get path to dataset files at specific version.

        Args:
            dataset: Dataset name.
            version_tag: Snapshot version tag.

        Returns:
            Path to the snapshot's files directory for this dataset.

        Raises:
            SnapshotNotFoundError: If snapshot doesn't exist.
            DatasetNotInSnapshotError: If dataset not in snapshot.
        """
        snapshot = self.get_snapshot(version_tag)
        if snapshot is None:
            raise SnapshotNotFoundError(version_tag)

        if dataset not in snapshot.datasets:
            raise DatasetNotInSnapshotError(version_tag, dataset)

        return self.snapshots_dir / version_tag / "files"

    def list_snapshots(self, include_referenced: bool = True) -> list[SnapshotManifest]:
        """List all available snapshots, ordered by creation date."""
        snapshots: list[SnapshotManifest] = []

        for path in self.snapshots_dir.iterdir():
            if path.is_dir() and not path.name.startswith("."):
                manifest_path = path / "manifest.json"
                if manifest_path.exists():
                    try:
                        with open(manifest_path) as f:
                            data = json.load(f)
                        snapshot = SnapshotManifest.model_validate(data)
                        if include_referenced or not snapshot.referenced_by:
                            snapshots.append(snapshot)
                    except Exception as e:
                        logger.warning("Failed to load snapshot %s: %s", path.name, e)

        # Sort by creation date, newest first
        snapshots.sort(key=lambda s: s.created_at, reverse=True)
        return snapshots

    # =========================================================================
    # Time-Travel Queries
    # =========================================================================

    def query_as_of(
        self,
        dataset: str,
        as_of_date: date,
    ) -> tuple[Path, SnapshotManifest]:
        """Get dataset path as it existed on given date.

        Semantics:
        - Returns latest snapshot where created_at <= as_of_date 23:59:59 UTC
        - Only considers date-based tags (YYYY-MM-DD format)
        - Among candidates, prefers snapshots containing the dataset

        Args:
            dataset: Dataset name.
            as_of_date: Date to query as of.

        Returns:
            Tuple of (data_path, snapshot_manifest).

        Raises:
            SnapshotNotFoundError: No snapshot exists <= as_of_date.
            DatasetNotInSnapshotError: No snapshot containing the dataset exists.
        """
        # Get all date-based snapshots
        candidates = [
            s
            for s in self.list_snapshots()
            if self._is_date_based_tag(s.version_tag) and s.created_at.date() <= as_of_date
        ]

        if not candidates:
            raise SnapshotNotFoundError(f"No snapshot exists on or before {as_of_date}")

        # Already sorted newest first by list_snapshots
        # Iterate through candidates to find one containing the dataset
        for snapshot in candidates:
            if dataset in snapshot.datasets:
                return self.get_data_at_version(dataset, snapshot.version_tag), snapshot

        # No snapshot contains the dataset - report the newest one for error context
        raise DatasetNotInSnapshotError(candidates[0].version_tag, dataset)

    def _is_date_based_tag(self, version_tag: str) -> bool:
        """Check if version tag is date-based (YYYY-MM-DD)."""
        return bool(self.DATE_TAG_PATTERN.match(version_tag))

    # =========================================================================
    # Backtest Linkage
    # =========================================================================

    def link_backtest(
        self,
        backtest_id: str,
        version_tag: str,
        datasets: list[str] | None = None,
    ) -> BacktestLinkage:
        """Atomically link backtest to snapshot.

        Args:
            backtest_id: Unique backtest identifier.
            version_tag: Snapshot version to link.
            datasets: Specific datasets to link (default: all in snapshot).

        Returns:
            BacktestLinkage object.

        Raises:
            SnapshotNotFoundError: If snapshot doesn't exist.
            ValueError: If backtest_id or version_tag is invalid.
        """
        # Validate backtest_id to prevent path traversal
        self._validate_identifier(backtest_id, self.backtests_dir, "backtest_id")

        # Use snapshot lock to prevent race conditions with delete_snapshot
        # and concurrent link_backtest operations
        with self._acquire_snapshot_lock():
            snapshot = self.get_snapshot(version_tag)
            if snapshot is None:
                raise SnapshotNotFoundError(version_tag)

            # Determine datasets
            if datasets is None:
                datasets = list(snapshot.datasets.keys())

            # Build dataset versions map
            dataset_versions = {
                ds: snapshot.datasets[ds].sync_manifest_version
                for ds in datasets
                if ds in snapshot.datasets
            }

            # Create linkage
            now = datetime.now(UTC)
            linkage = BacktestLinkage(
                backtest_id=backtest_id,
                created_at=now,
                snapshot_version=version_tag,
                dataset_versions=dataset_versions,
                checksum=self._compute_linkage_checksum(backtest_id, version_tag, dataset_versions),
            )

            # Write linkage file atomically
            linkage_path = self.backtests_dir / f"{backtest_id}.json"
            self._atomic_write_json(linkage_path, linkage.model_dump(mode="json"))

            # Update snapshot's referenced_by
            snapshot.referenced_by.append(backtest_id)
            snapshot_manifest_path = self.snapshots_dir / version_tag / "manifest.json"
            self._atomic_write_json(snapshot_manifest_path, snapshot.model_dump(mode="json"))

            # Update backtest index
            self._update_backtest_index(backtest_id, version_tag)

            logger.info(
                "Linked backtest to snapshot",
                extra={"backtest_id": backtest_id, "version_tag": version_tag},
            )

            return linkage

    def get_snapshot_for_backtest(self, backtest_id: str) -> SnapshotManifest | None:
        """Get snapshot linked to backtest.

        Args:
            backtest_id: Unique backtest identifier.

        Returns:
            SnapshotManifest or None if backtest not found.

        Raises:
            ValueError: If backtest_id is invalid (path traversal attempt).
        """
        # Validate backtest_id to prevent path traversal
        self._validate_identifier(backtest_id, self.backtests_dir, "backtest_id")

        linkage_path = self.backtests_dir / f"{backtest_id}.json"
        if not linkage_path.exists():
            return None

        with open(linkage_path) as f:
            data = json.load(f)

        linkage = BacktestLinkage.model_validate(data)
        return self.get_snapshot(linkage.snapshot_version)

    def get_backtests_for_snapshot(self, version_tag: str) -> list[str]:
        """Get all backtest IDs referencing a snapshot."""
        snapshot = self.get_snapshot(version_tag)
        if snapshot is None:
            return []
        return snapshot.referenced_by.copy()

    def _compute_linkage_checksum(
        self,
        backtest_id: str,
        version_tag: str,
        dataset_versions: dict[str, int],
    ) -> str:
        """Compute checksum for backtest linkage."""
        content = f"{backtest_id}:{version_tag}:{json.dumps(dataset_versions, sort_keys=True)}"
        return hashlib.sha256(content.encode()).hexdigest()

    def _update_backtest_index(self, backtest_id: str, version_tag: str) -> None:
        """Update the backtest index file."""
        index_path = self.backtests_dir / "_index.json"

        index: dict[str, str] = {}
        if index_path.exists():
            with open(index_path) as f:
                index = json.load(f)

        index[backtest_id] = version_tag
        self._atomic_write_json(index_path, index)

    def _clean_backtest_index_for_snapshot(self, version_tag: str) -> int:
        """Remove backtest index entries pointing to a deleted snapshot.

        Args:
            version_tag: The snapshot being deleted.

        Returns:
            Number of entries removed.
        """
        index_path = self.backtests_dir / "_index.json"

        if not index_path.exists():
            return 0

        with open(index_path) as f:
            index: dict[str, str] = json.load(f)

        # Find and remove entries pointing to this snapshot
        to_remove = [
            backtest_id for backtest_id, snap_tag in index.items() if snap_tag == version_tag
        ]

        if not to_remove:
            return 0

        for backtest_id in to_remove:
            del index[backtest_id]

        self._atomic_write_json(index_path, index)
        return len(to_remove)

    # =========================================================================
    # Snapshot Deletion
    # =========================================================================

    def delete_snapshot(self, version_tag: str, force: bool = False) -> bool:
        """Delete snapshot with full cleanup.

        Args:
            version_tag: Snapshot to delete.
            force: If True, bypasses reference check (emergency only).

        Returns:
            True if deleted successfully.

        Raises:
            SnapshotNotFoundError: If snapshot doesn't exist.
            SnapshotReferencedError: If snapshot is referenced and force=False.
        """
        # Validate snapshot exists first (outside lock for fast fail)
        snapshot = self.get_snapshot(version_tag)
        if snapshot is None:
            raise SnapshotNotFoundError(version_tag)

        with self._acquire_snapshot_lock():
            try:
                # Re-read snapshot inside lock to get current referenced_by state
                # This prevents race condition with concurrent link_backtest
                snapshot = self.get_snapshot(version_tag)
                if snapshot is None:
                    raise SnapshotNotFoundError(version_tag)

                # Block deletion if referenced (unless force)
                # Check MUST be inside lock to prevent TOCTOU race
                if snapshot.referenced_by and not force:
                    raise SnapshotReferencedError(version_tag, snapshot.referenced_by)

                if force and snapshot.referenced_by:
                    logger.warning(
                        "Force-deleting referenced snapshot",
                        extra={
                            "version_tag": version_tag,
                            "referenced_by": snapshot.referenced_by,
                        },
                    )

                # Collect CAS hashes to release BEFORE deletion
                cas_hashes_to_release: list[str] = []
                for ds in snapshot.datasets.values():
                    for file_info in ds.files:
                        if file_info.storage_mode == "cas":
                            cas_hashes_to_release.append(file_info.target)

                # 1. Delete snapshot directory FIRST
                # If this fails, CAS refs are preserved (data not orphaned)
                snapshot_path = self.snapshots_dir / version_tag
                shutil.rmtree(snapshot_path)

                # 2. Release CAS refs AFTER successful directory deletion
                # If this fails, we have orphaned refs (safe - just leaked storage)
                if cas_hashes_to_release:
                    try:
                        cas_index = self._load_cas_index()
                        for cas_hash in cas_hashes_to_release:
                            if cas_hash in cas_index.files:
                                entry = cas_index.files[cas_hash]
                                entry.ref_count -= 1
                                if version_tag in entry.referencing_snapshots:
                                    entry.referencing_snapshots.remove(version_tag)
                        self._save_cas_index(cas_index)
                    except Exception as e:
                        logger.warning("Failed to release CAS refs after deletion: %s", e)

                # 3. Orphan backtest linkages (advisory, non-critical)
                for backtest_id in snapshot.referenced_by:
                    try:
                        self._mark_backtest_orphaned(backtest_id, version_tag)
                    except Exception as e:
                        logger.warning("Failed to orphan backtest %s: %s", backtest_id, e)

                # 4. Clean backtest index (advisory, non-critical)
                try:
                    removed = self._clean_backtest_index_for_snapshot(version_tag)
                    if removed > 0:
                        logger.debug("Removed %d entries from backtest index", removed)
                except Exception as e:
                    logger.warning("Failed to clean backtest index: %s", e)

                # 5. Mark related diffs as orphaned (advisory, non-critical)
                try:
                    self._mark_diffs_orphaned(version_tag)
                except Exception as e:
                    logger.warning("Failed to orphan diffs: %s", e)

                logger.info("Deleted snapshot", extra={"version_tag": version_tag})
                return True

            except Exception as e:
                logger.error(
                    "Snapshot deletion failed",
                    extra={"version_tag": version_tag, "error": str(e)},
                )
                raise

    def _mark_backtest_orphaned(self, backtest_id: str, version_tag: str) -> None:
        """Mark a backtest linkage as orphaned."""
        linkage_path = self.backtests_dir / f"{backtest_id}.json"
        if not linkage_path.exists():
            return

        with open(linkage_path) as f:
            data = json.load(f)

        linkage = BacktestLinkage.model_validate(data)
        linkage.orphaned_at = datetime.now(UTC)
        self._atomic_write_json(linkage_path, linkage.model_dump(mode="json"))

    def _mark_diffs_orphaned(self, version_tag: str) -> None:
        """Mark diffs referencing this snapshot as orphaned."""
        for diff_path in self.diffs_dir.glob("*.json*"):
            try:
                # Handle both .json and .json.zst
                if diff_path.suffix == ".zst":
                    continue  # Skip compressed diffs for now

                with open(diff_path) as f:
                    data = json.load(f)

                diff = SnapshotDiff.model_validate(data)
                if diff.from_version == version_tag or diff.to_version == version_tag:
                    diff.orphaned_at = datetime.now(UTC)
                    self._atomic_write_json(diff_path, diff.model_dump(mode="json"))
            except Exception as e:
                logger.warning("Failed to process diff %s: %s", diff_path, e)

    # =========================================================================
    # Retention Policy
    # =========================================================================

    def enforce_retention_policy(self) -> list[str]:
        """Delete unreferenced snapshots older than RETENTION_DAYS.

        Returns:
            List of deleted version tags.
        """
        deleted: list[str] = []
        cutoff = datetime.now(UTC) - timedelta(days=self.RETENTION_DAYS)

        for snapshot in self.list_snapshots(include_referenced=True):
            # Skip referenced snapshots
            if snapshot.referenced_by:
                continue

            # Skip recent snapshots
            if snapshot.created_at > cutoff:
                continue

            try:
                self.delete_snapshot(snapshot.version_tag)
                deleted.append(snapshot.version_tag)
            except Exception as e:
                logger.warning(
                    "Failed to delete snapshot %s during retention: %s",
                    snapshot.version_tag,
                    e,
                )

        if deleted:
            logger.info("Retention policy deleted %d snapshots", len(deleted))

        return deleted

    # =========================================================================
    # Integrity Verification
    # =========================================================================

    def verify_snapshot_integrity(self, version_tag: str) -> list[str]:
        """Verify all checksums match for a snapshot.

        Returns:
            List of error messages (empty = all OK).
        """
        snapshot = self.get_snapshot(version_tag)
        if snapshot is None:
            return [f"Snapshot not found: {version_tag}"]

        errors: list[str] = []
        snapshot_files_dir = self.snapshots_dir / version_tag / "files"

        all_files: list[FileStorageInfo] = []
        for ds in snapshot.datasets.values():
            all_files.extend(ds.files)

        for file_info in all_files:
            try:
                # Get actual file path based on storage mode
                if file_info.storage_mode == "cas":
                    # Derive extension from original path
                    original = Path(file_info.original_path)
                    file_path = self._get_cas_path(file_info.target, original)
                else:
                    file_path = snapshot_files_dir / file_info.path

                if not file_path.exists():
                    errors.append(f"Missing file: {file_info.path}")
                    continue

                # Verify checksum
                actual_checksum = self.validator.compute_checksum(file_path)
                if actual_checksum != file_info.checksum:
                    errors.append(
                        f"Checksum mismatch for {file_info.path}: "
                        f"expected {file_info.checksum}, got {actual_checksum}"
                    )

            except Exception as e:
                errors.append(f"Error verifying {file_info.path}: {e}")

        # Verify aggregate checksum
        expected_aggregate = self._compute_aggregate_checksum(all_files)
        if expected_aggregate != snapshot.aggregate_checksum:
            errors.append(
                f"Aggregate checksum mismatch: "
                f"expected {snapshot.aggregate_checksum}, got {expected_aggregate}"
            )

        return errors

    # =========================================================================
    # CAS Operations
    # =========================================================================

    def _get_cas_path(self, checksum: str, original_path: Path | None = None) -> Path:
        """Get CAS storage path for a file.

        Uses ONLY checksum as filename (no extension) to ensure consistent lookup.
        This prevents issues where the same content stored via different file
        extensions (e.g., .parquet vs .csv) would result in different CAS paths,
        breaking integrity verification on deduplicated files.

        Args:
            checksum: File checksum (used as filename).
            original_path: Ignored - kept for API compatibility.

        Returns:
            Path to CAS file (checksum only, no extension).
        """
        # NOTE: original_path parameter is intentionally ignored.
        # CAS files use checksum-only naming for consistent deduplication.
        _ = original_path  # Suppress unused parameter warning
        return self.cas_dir / checksum

    def _store_in_cas(self, file_path: Path, checksum: str, version_tag: str) -> str:
        """Store file in content-addressable storage (non-batched).

        Returns:
            The CAS hash (same as checksum).
        """
        cas_path = self._get_cas_path(checksum, file_path)

        # Load or create index
        index = self._load_cas_index()

        if checksum in index.files:
            # File already exists, increment ref count
            entry = index.files[checksum]
            entry.ref_count += 1
            entry.referencing_snapshots.append(version_tag)
        else:
            # Copy file to CAS safely (temp+rename+checksum verify)
            self._safe_copy_to_cas(file_path, cas_path, checksum)

            # Create entry
            entry = CASEntry(
                hash=checksum,
                size_bytes=file_path.stat().st_size,
                original_path=str(file_path),
                created_at=datetime.now(UTC),
                ref_count=1,
                referencing_snapshots=[version_tag],
            )
            index.files[checksum] = entry
            index.total_size_bytes += entry.size_bytes

        # Save index
        self._save_cas_index(index)

        logger.debug("Stored in CAS: %s (ref_count=%d)", checksum, entry.ref_count)
        return checksum

    def _store_in_cas_batched(
        self,
        file_path: Path,
        checksum: str,
        version_tag: str,
        cas_index: CASIndex,
        cas_hashes_added: list[str],
        existing_cas_hashes: set[str],
        cas_new_files: list[str],
    ) -> str:
        """Store file in CAS with batched index updates.

        Mutates cas_index in place (caller must save at end).
        Tracks added hashes in cas_hashes_added for cleanup on failure.
        Tracks new files (not in existing) in cas_new_files.

        Returns:
            The CAS hash (same as checksum).
        """
        cas_path = self._get_cas_path(checksum, file_path)
        is_new_file = checksum not in existing_cas_hashes

        if checksum in cas_index.files:
            # File already exists in memory index, increment ref count
            entry = cas_index.files[checksum]
            entry.ref_count += 1
            entry.referencing_snapshots.append(version_tag)
        else:
            # Copy file to CAS safely (temp+rename+checksum verify)
            self._safe_copy_to_cas(file_path, cas_path, checksum)

            # Create entry
            entry = CASEntry(
                hash=checksum,
                size_bytes=file_path.stat().st_size,
                original_path=str(file_path),
                created_at=datetime.now(UTC),
                ref_count=1,
                referencing_snapshots=[version_tag],
            )
            cas_index.files[checksum] = entry
            cas_index.total_size_bytes += entry.size_bytes

        # Track for cleanup on failure
        cas_hashes_added.append(checksum)

        # Track NEW files (need to delete from disk if unpersisted failure)
        if is_new_file:
            cas_new_files.append(checksum)

        logger.debug(
            "Stored in CAS (batched): %s (ref_count=%d, new=%s)",
            checksum,
            entry.ref_count,
            is_new_file,
        )
        return checksum

    def _release_cas_ref(self, cas_hash: str, version_tag: str) -> None:
        """Release a CAS reference (decrement ref count)."""
        index = self._load_cas_index()

        if cas_hash not in index.files:
            return

        entry = index.files[cas_hash]
        entry.ref_count -= 1
        if version_tag in entry.referencing_snapshots:
            entry.referencing_snapshots.remove(version_tag)

        self._save_cas_index(index)

    def gc_cas(self) -> int:
        """Garbage collect unreferenced CAS files.

        Cleans up both:
        1. Index entries with ref_count <= 0
        2. Orphaned files on disk not present in the index (crash recovery)

        Must hold snapshot lock to prevent race with create_snapshot.

        Returns:
            Total bytes freed (indexed + orphaned).
        """
        with self._acquire_snapshot_lock():
            index = self._load_cas_index()
            bytes_freed_index = 0  # Track indexed bytes separately
            bytes_freed_orphan = 0  # Track orphan bytes separately
            to_delete: list[str] = []

            # Clean up entries with ref_count <= 0
            for cas_hash, entry in index.files.items():
                if entry.ref_count <= 0:
                    # Glob for hash with any extension (extension varies by file type)
                    for cas_path in self.cas_dir.glob(f"{cas_hash}.*"):
                        if cas_path.exists():
                            cas_path.unlink()
                    # Always subtract entry size from index (whether file exists or not)
                    bytes_freed_index += entry.size_bytes
                    to_delete.append(cas_hash)

            for cas_hash in to_delete:
                del index.files[cas_hash]

            # Also scan for orphaned files not in index (crash recovery)
            orphan_count = 0
            known_hashes = set(index.files.keys())
            for cas_file in self.cas_dir.iterdir():
                # Skip index files and directories
                if cas_file.name.startswith("_") or cas_file.is_dir():
                    continue
                # Extract hash from filename (format: {hash}.{ext})
                file_hash = cas_file.stem
                if file_hash not in known_hashes:
                    try:
                        file_size = cas_file.stat().st_size
                        cas_file.unlink()
                        bytes_freed_orphan += file_size
                        orphan_count += 1
                        logger.debug("Deleted orphaned CAS file: %s", file_hash)
                    except Exception as e:
                        logger.warning("Failed to delete orphaned CAS file %s: %s", file_hash, e)

            # Only subtract indexed bytes from total (orphans were never counted)
            index.total_size_bytes -= bytes_freed_index
            index.last_gc_at = datetime.now(UTC)
            self._save_cas_index(index)

            total_freed = bytes_freed_index + bytes_freed_orphan
            if total_freed > 0:
                logger.info(
                    "CAS GC freed %d bytes (%d indexed + %d orphaned files)",
                    total_freed,
                    len(to_delete),
                    orphan_count,
                )

            return total_freed

    def _load_cas_index(self) -> CASIndex:
        """Load CAS index from disk."""
        index_path = self.cas_dir / "_refcount.json"
        if not index_path.exists():
            return CASIndex()

        with open(index_path) as f:
            data = json.load(f)

        return CASIndex.model_validate(data)

    def _save_cas_index(self, index: CASIndex) -> None:
        """Save CAS index to disk atomically."""
        index_path = self.cas_dir / "_refcount.json"
        self._atomic_write_json(index_path, index.model_dump(mode="json"))

    # =========================================================================
    # Helper Methods
    # =========================================================================

    def _compute_aggregate_checksum(self, files: list[FileStorageInfo]) -> str:
        """Compute aggregate checksum for tamper detection."""
        sorted_files = sorted(files, key=lambda f: f.path)
        manifest_str = "\n".join(f"{f.path}:{f.checksum}" for f in sorted_files)
        return hashlib.sha256(manifest_str.encode()).hexdigest()

    def _get_latest_snapshot_checksum(self) -> str | None:
        """Get aggregate checksum of most recent snapshot."""
        snapshots = self.list_snapshots()
        if not snapshots:
            return None
        return snapshots[0].aggregate_checksum

    def _atomic_write_json(self, path: Path, data: Any) -> None:
        """Atomically write JSON data to file."""
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

            Path(temp_path).rename(path)
            self._fsync_directory(path.parent)

        except Exception:
            if Path(temp_path).exists():
                Path(temp_path).unlink()
            raise

    def _fsync_directory(self, dir_path: Path) -> None:
        """Sync directory for crash safety."""
        try:
            fd = os.open(str(dir_path), os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        except OSError:
            pass

    def _fsync_file(self, file_path: Path) -> None:
        """Sync a file to ensure durability."""
        fd = os.open(str(file_path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _copy_with_fsync(
        self,
        src: Path,
        dest: Path,
        expected_checksum: str | None = None,
    ) -> None:
        """Copy a file with fsync for durability and optional checksum verification.

        Uses shutil.copy2 to preserve metadata, then fsyncs the
        destination file and its parent directory.

        Args:
            src: Source file path.
            dest: Destination file path.
            expected_checksum: If provided, verify copied file matches this checksum.

        Raises:
            ValueError: If checksum verification fails.
        """
        shutil.copy2(str(src), str(dest))
        self._fsync_file(dest)
        self._fsync_directory(dest.parent)

        # Verify checksum if provided (critical for immutability)
        if expected_checksum is not None:
            actual_checksum = self.validator.compute_checksum(dest)
            if actual_checksum != expected_checksum:
                # Clean up the bad copy
                dest.unlink(missing_ok=True)
                raise ValueError(
                    f"Copy checksum mismatch: expected {expected_checksum}, "
                    f"got {actual_checksum}"
                )

    def _safe_copy_to_cas(
        self,
        src: Path,
        dest: Path,
        expected_checksum: str,
    ) -> None:
        """Safely copy a file to CAS with temp+rename+checksum verification.

        1. Copy to a temp file in the CAS directory
        2. Fsync the temp file
        3. Verify checksum matches expected
        4. Rename to final destination (atomic on POSIX)
        5. Fsync directory

        Args:
            src: Source file path.
            dest: Destination path in CAS.
            expected_checksum: Expected SHA-256 checksum to verify.

        Raises:
            ValueError: If checksum doesn't match after copy.
            OSError: If copy or rename fails.
        """
        # Create temp file in same directory for atomic rename
        temp_path = dest.parent / f".tmp_{dest.name}_{os.getpid()}"

        try:
            # Copy to temp
            shutil.copy2(str(src), str(temp_path))
            self._fsync_file(temp_path)

            # Verify checksum
            actual_checksum = self.validator.compute_checksum(temp_path)
            if actual_checksum != expected_checksum:
                raise ValueError(
                    f"CAS checksum mismatch: expected {expected_checksum}, "
                    f"got {actual_checksum}"
                )

            # Atomic rename
            temp_path.rename(dest)
            self._fsync_directory(dest.parent)

        except Exception:
            # Clean up temp file on any failure
            if temp_path.exists():
                temp_path.unlink()
            raise

    # Stale lock timeout: used ONLY for distributed environments where PID check
    # is not reliable (different hostname). Much longer than normal operation.
    STALE_LOCK_TIMEOUT_SECONDS = 3600.0  # 1 hour - only for cross-host fallback

    def _get_hostname(self) -> str:
        """Get current hostname for lock identification."""
        import socket

        return socket.gethostname()

    def _is_process_alive(self, pid: int) -> bool:
        """Check if a process with given PID is still alive."""
        try:
            os.kill(pid, 0)  # Signal 0 = check existence only
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            # Process exists but we can't signal it (different user)
            return True

    def _is_lock_stale(self, lock_path: Path) -> bool:
        """Check if a lock file is stale (dead process).

        Staleness is determined by:
        1. If hostname matches: check if PID is alive (most reliable)
        2. If hostname differs: use timeout as fallback (distributed environment)
        3. If lock data is corrupted: treat as stale

        CRITICAL: We do NOT evict locks just because they're "old" when the
        process is alive. Long-running operations (large snapshots, GC) must
        not have their locks stolen.

        Returns:
            True if lock is stale and should be removed.
        """
        try:
            with open(lock_path) as f:
                lock_data = json.load(f)

            pid = lock_data.get("pid")
            lock_hostname = lock_data.get("hostname")
            acquired_at_str = lock_data.get("acquired_at")
            current_hostname = self._get_hostname()

            # Same host: we can reliably check if the process is alive
            if lock_hostname == current_hostname:
                if pid is not None:
                    if not self._is_process_alive(pid):
                        logger.warning(
                            "Detected stale lock from dead process",
                            extra={"pid": pid, "hostname": lock_hostname},
                        )
                        return True
                    else:
                        # Process is alive - lock is NOT stale, even if old
                        return False
                # No PID in lock data - corrupted, treat as stale
                logger.warning("Lock file missing PID, treating as stale")
                return True

            # Different host: we can't verify PID, use timeout as fallback
            # This is the ONLY case where we use time-based eviction
            if acquired_at_str:
                acquired_at = datetime.fromisoformat(acquired_at_str)
                age_seconds = (datetime.now(UTC) - acquired_at).total_seconds()
                if age_seconds > self.STALE_LOCK_TIMEOUT_SECONDS:
                    logger.warning(
                        "Detected stale lock from different host (timeout)",
                        extra={
                            "age_seconds": age_seconds,
                            "pid": pid,
                            "lock_hostname": lock_hostname,
                            "current_hostname": current_hostname,
                        },
                    )
                    return True

            # Lock held by different host, not timed out - not stale
            return False

        except (json.JSONDecodeError, OSError, ValueError) as e:
            # Corrupted or unreadable lock file - treat as stale
            logger.warning("Lock file corrupted or unreadable: %s", e)
            return True

    @contextmanager
    def _acquire_snapshot_lock(self) -> Iterator[None]:
        """Acquire exclusive lock for snapshot operations.

        Features stale lock detection and recovery:
        - Detects locks held by dead processes (same host)
        - Uses timeout for locks from different hosts (distributed fallback)
        - Safely removes stale locks and retries
        """
        lock_path = self.locks_dir / "snapshots.lock"
        lock_data = {
            "pid": os.getpid(),
            "hostname": self._get_hostname(),
            "acquired_at": datetime.now(UTC).isoformat(),
        }

        start = datetime.now(UTC)
        while (datetime.now(UTC) - start).total_seconds() < self.SNAPSHOT_LOCK_TIMEOUT_SECONDS:
            try:
                fd = os.open(
                    str(lock_path),
                    os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                    0o644,
                )
                try:
                    os.write(fd, json.dumps(lock_data).encode())
                    os.fsync(fd)
                finally:
                    os.close(fd)
                break
            except FileExistsError:
                # Lock exists - check if stale
                if self._is_lock_stale(lock_path):
                    try:
                        lock_path.unlink()
                        logger.info("Removed stale lock file")
                        # Don't sleep - immediately retry
                        continue
                    except OSError as e:
                        logger.warning("Failed to remove stale lock: %s", e)
                # Lock is held by active process - wait and retry
                import time

                time.sleep(0.1)
        else:
            raise LockNotHeldError("Failed to acquire snapshot lock")

        try:
            yield
        finally:
            lock_path.unlink(missing_ok=True)
