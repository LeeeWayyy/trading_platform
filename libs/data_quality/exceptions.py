"""
Custom exceptions for the data quality framework.

These exceptions extend DataQualityError from libs.common.exceptions
and provide specific error types for sync validation, schema drift,
checksum verification, quarantine operations, locking, and disk space.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from libs.common.exceptions import DataQualityError

if TYPE_CHECKING:
    from libs.data_quality.schema import SchemaDrift
    from libs.data_quality.validation import ValidationError


class SyncValidationError(DataQualityError):
    """Raised when sync validation fails.

    Attributes:
        errors: List of ValidationError objects describing each failure.
    """

    def __init__(
        self, errors: list[ValidationError], message: str = ""
    ) -> None:
        self.errors = errors
        super().__init__(message or f"Validation failed: {len(errors)} error(s)")


class SchemaError(DataQualityError):
    """Raised when schema drift is breaking (removed/changed columns).

    Attributes:
        drift: SchemaDrift object describing the detected changes.
    """

    def __init__(self, drift: SchemaDrift, message: str = "") -> None:
        self.drift = drift
        super().__init__(message or f"Breaking schema drift: {drift}")


class ChecksumMismatchError(DataQualityError):
    """Raised when file checksum doesn't match expected.

    Attributes:
        file_path: Path to the file that failed verification.
        expected: Expected checksum value.
        actual: Actual computed checksum value.
    """

    def __init__(self, file_path: str, expected: str, actual: str) -> None:
        self.file_path = file_path
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Checksum mismatch for {file_path}: expected {expected}, got {actual}"
        )


class QuarantineError(DataQualityError):
    """Raised when quarantine operation fails.

    This can occur when moving failed sync data to the quarantine directory
    fails due to disk space, permissions, or other I/O errors.
    """

    pass


class LockNotHeldError(DataQualityError):
    """Raised when required lock is not held.

    Operations that require exclusive access (like manifest saves)
    raise this error if the caller doesn't hold a valid lock.
    """

    pass


class DiskSpaceError(DataQualityError):
    """Raised when disk space is insufficient.

    Raised when disk usage exceeds the blocked threshold (95%)
    and write operations cannot proceed safely.
    """

    pass


class DataNotFoundError(DataQualityError):
    """Raised when requested data is not available.

    This can occur when:
    - No manifest exists for a dataset (run sync first)
    - Requested PERMNO/ticker is not found
    - No data available for the specified date range
    """

    pass


# =============================================================================
# Versioning Exceptions (T1.6)
# =============================================================================


class SnapshotNotFoundError(DataNotFoundError):
    """Raised when requested snapshot version doesn't exist.

    Attributes:
        version_tag: The requested version tag that was not found.
    """

    def __init__(self, version_tag: str) -> None:
        self.version_tag = version_tag
        super().__init__(f"Snapshot not found: {version_tag}")


class SnapshotReferencedError(DataQualityError):
    """Raised when attempting to delete a referenced snapshot.

    Snapshots that are linked to backtests cannot be deleted unless
    force=True is specified.

    Attributes:
        version_tag: The snapshot that cannot be deleted.
        referenced_by: List of backtest IDs referencing this snapshot.
    """

    def __init__(self, version_tag: str, referenced_by: list[str]) -> None:
        self.version_tag = version_tag
        self.referenced_by = referenced_by
        super().__init__(
            f"Cannot delete snapshot {version_tag}: referenced by {referenced_by}"
        )


class SnapshotCorruptedError(DataQualityError):
    """Raised when snapshot integrity verification fails.

    This indicates checksum mismatch or missing files in the snapshot.

    Attributes:
        version_tag: The corrupted snapshot.
        details: Description of the corruption.
    """

    def __init__(self, version_tag: str, details: str) -> None:
        self.version_tag = version_tag
        self.details = details
        super().__init__(f"Snapshot {version_tag} is corrupted: {details}")


class SnapshotInconsistentError(DataQualityError):
    """Raised when source data changed during snapshot creation.

    Uses optimistic concurrency control - if a dataset's manifest version
    changes during snapshot creation, the operation is aborted.

    Attributes:
        dataset: Dataset that was modified.
        expected_version: Version at snapshot start.
        actual_version: Version at snapshot end.
    """

    def __init__(
        self, dataset: str, expected_version: int, actual_version: int
    ) -> None:
        self.dataset = dataset
        self.expected_version = expected_version
        self.actual_version = actual_version
        super().__init__(
            f"Dataset {dataset} was modified during snapshot "
            f"(expected version {expected_version}, got {actual_version})"
        )


class DatasetNotInSnapshotError(DataNotFoundError):
    """Raised when snapshot exists but doesn't contain requested dataset.

    Attributes:
        version_tag: The snapshot that was found.
        dataset: The dataset that was not in the snapshot.
    """

    def __init__(self, version_tag: str, dataset: str) -> None:
        self.version_tag = version_tag
        self.dataset = dataset
        super().__init__(f"Snapshot {version_tag} does not contain dataset {dataset}")


class SnapshotRecoveryError(DataQualityError):
    """Raised when snapshot recovery fails.

    Recovery can fail if:
    - No valid source snapshot exists
    - Required diffs are missing
    - Checksum verification fails after recovery

    Attributes:
        version_tag: The snapshot that failed to recover.
        reason: Description of why recovery failed.
    """

    def __init__(self, version_tag: str, reason: str) -> None:
        self.version_tag = version_tag
        self.reason = reason
        super().__init__(f"Failed to recover snapshot {version_tag}: {reason}")
