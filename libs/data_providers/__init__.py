"""
Data providers for WRDS academic data sources.

This module provides:
- AtomicFileLock: OS-atomic file locking for single-writer access
- WRDSClient: Connection wrapper with pooling and rate limiting
- SyncManager: Bulk data sync with atomic writes and progress tracking
- CRSPLocalProvider: Read-only CRSP data access with DuckDB
- CompustatLocalProvider: Read-only Compustat fundamental data access with DuckDB
"""

from libs.data_providers.compustat_local_provider import (
    AmbiguousGVKEYError,
    CompustatLocalProvider,
)
from libs.data_providers.compustat_local_provider import (
    ManifestVersionChangedError as CompustatManifestVersionChangedError,
)
from libs.data_providers.crsp_local_provider import (
    AmbiguousTickerError,
    CRSPLocalProvider,
    ManifestVersionChangedError,
)
from libs.data_providers.locking import (
    AtomicFileLock,
    LockAcquisitionError,
    LockRecoveryError,
    MalformedLockFileError,
    atomic_lock,
)
from libs.data_providers.sync_manager import SyncManager, SyncProgress
from libs.data_providers.wrds_client import WRDSClient, WRDSConfig

__all__ = [
    # CRSP Local Provider
    "CRSPLocalProvider",
    "AmbiguousTickerError",
    "ManifestVersionChangedError",
    # Compustat Local Provider
    "CompustatLocalProvider",
    "AmbiguousGVKEYError",
    "CompustatManifestVersionChangedError",
    # Locking
    "AtomicFileLock",
    "atomic_lock",
    "LockAcquisitionError",
    "LockRecoveryError",
    "MalformedLockFileError",
    # WRDS Client
    "WRDSClient",
    "WRDSConfig",
    # Sync Manager
    "SyncManager",
    "SyncProgress",
]
