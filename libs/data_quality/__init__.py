"""
Data quality and validation framework for WRDS data syncs.

This module provides:
- SyncManifest: Pydantic model tracking data sync state
- ManifestManager: Atomic manifest operations with locking
- DataValidator: Data validation (row counts, nulls, schema, dates)
- SchemaRegistry: Schema versioning and drift detection
- Various exceptions for error handling
"""

from libs.data_quality.exceptions import (
    ChecksumMismatchError,
    DataNotFoundError,
    DiskSpaceError,
    LockNotHeldError,
    QuarantineError,
    SchemaError,
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
]
