"""
Data providers for WRDS academic data sources.

This module provides:
- AtomicFileLock: OS-atomic file locking for single-writer access
- WRDSClient: Connection wrapper with pooling and rate limiting
- SyncManager: Bulk data sync with atomic writes and progress tracking
"""

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
