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
