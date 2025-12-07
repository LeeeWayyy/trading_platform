"""
Data quality and validation framework for WRDS data syncs.

This module provides:
- SyncManifest: Pydantic model tracking data sync state
- ManifestManager: Atomic manifest operations with locking
- DataValidator: Data validation (row counts, nulls, schema, dates)
- SchemaRegistry: Schema versioning and drift detection
- DatasetVersionManager: Dataset versioning for reproducibility (T1.6)
- Various exceptions for error handling
"""

from libs.data_quality.exceptions import (
    ChecksumMismatchError,
    DataNotFoundError,
    DatasetNotInSnapshotError,
    DiskSpaceError,
    LockNotHeldError,
    QuarantineError,
    SchemaError,
    SnapshotCorruptedError,
    SnapshotInconsistentError,
    SnapshotNotFoundError,
    SnapshotRecoveryError,
    SnapshotReferencedError,
    SyncValidationError,
)
from libs.data_quality.manifest import ManifestManager, SyncManifest
from libs.data_quality.schema import DatasetSchema, SchemaDrift, SchemaRegistry
from libs.data_quality.types import (
    DiskSpaceStatus,
    ExchangeCalendarAdapter,
    LockToken,
    TradingCalendar,
)
from libs.data_quality.validation import AnomalyAlert, DataValidator, ValidationError
from libs.data_quality.versioning import (
    BacktestLinkage,
    CASEntry,
    CASIndex,
    DatasetSnapshot,
    DatasetVersionManager,
    DiffFileEntry,
    FileStorageInfo,
    SnapshotDiff,
    SnapshotManifest,
)

__all__ = [
    # Manifest
    "SyncManifest",
    "ManifestManager",
    # Validation
    "DataValidator",
    "ValidationError",
    "AnomalyAlert",
    # Schema
    "SchemaRegistry",
    "DatasetSchema",
    "SchemaDrift",
    # Versioning (T1.6)
    "DatasetVersionManager",
    "SnapshotManifest",
    "DatasetSnapshot",
    "FileStorageInfo",
    "BacktestLinkage",
    "CASEntry",
    "CASIndex",
    "SnapshotDiff",
    "DiffFileEntry",
    # Types
    "LockToken",
    "DiskSpaceStatus",
    "TradingCalendar",
    "ExchangeCalendarAdapter",
    # Exceptions
    "SyncValidationError",
    "SchemaError",
    "ChecksumMismatchError",
    "QuarantineError",
    "LockNotHeldError",
    "DiskSpaceError",
    "DataNotFoundError",
    # Versioning Exceptions
    "SnapshotNotFoundError",
    "SnapshotReferencedError",
    "SnapshotCorruptedError",
    "SnapshotInconsistentError",
    "DatasetNotInSnapshotError",
    "SnapshotRecoveryError",
]
