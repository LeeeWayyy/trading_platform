"""
Data providers for WRDS academic data sources, Fama-French factors, and yfinance.

This module provides:
- AtomicFileLock: OS-atomic file locking for single-writer access
- WRDSClient: Connection wrapper with pooling and rate limiting
- SyncManager: Bulk data sync with atomic writes and progress tracking
- CRSPLocalProvider: Read-only CRSP data access with DuckDB
- CompustatLocalProvider: Read-only Compustat fundamental data access with DuckDB
- FamaFrenchLocalProvider: Read-only Fama-French factor data access
- YFinanceProvider: Free market data for development (NOT for production)
- UnifiedDataFetcher: Unified interface for data access with provider switching
- DataProvider: Protocol for data provider implementations
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
from libs.data_providers.fama_french_local_provider import (
    ChecksumError,
    FamaFrenchLocalProvider,
    FamaFrenchSyncError,
)
from libs.data_providers.locking import (
    AtomicFileLock,
    LockAcquisitionError,
    LockRecoveryError,
    MalformedLockFileError,
    atomic_lock,
)

# Unified Data Fetcher (P4T1.8)
from libs.data_providers.protocols import (
    UNIFIED_COLUMNS,
    UNIFIED_SCHEMA,
    ConfigurationError,
    CRSPDataProviderAdapter,
    DataProvider,
    DataProviderError,
    ProductionProviderRequiredError,
    ProviderNotSupportedError,
    ProviderUnavailableError,
    YFinanceDataProviderAdapter,
)
from libs.data_providers.sync_manager import SyncManager, SyncProgress
from libs.data_providers.unified_fetcher import (
    FetcherConfig,
    ProviderType,
    UnifiedDataFetcher,
)
from libs.data_providers.wrds_client import WRDSClient, WRDSConfig
from libs.data_providers.yfinance_provider import (
    DriftDetectedError,
    ProductionGateError,
    YFinanceError,
    YFinanceProvider,
)

__all__ = [
    # CRSP Local Provider
    "CRSPLocalProvider",
    "AmbiguousTickerError",
    "ManifestVersionChangedError",
    # Compustat Local Provider
    "CompustatLocalProvider",
    "AmbiguousGVKEYError",
    "CompustatManifestVersionChangedError",
    # Fama-French Local Provider
    "FamaFrenchLocalProvider",
    "FamaFrenchSyncError",
    "ChecksumError",
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
    # yfinance Provider (dev-only)
    "YFinanceProvider",
    "YFinanceError",
    "ProductionGateError",
    "DriftDetectedError",
    # Unified Data Fetcher (P4T1.8)
    "UnifiedDataFetcher",
    "FetcherConfig",
    "ProviderType",
    "DataProvider",
    "DataProviderError",
    "ProviderUnavailableError",
    "ProviderNotSupportedError",
    "ProductionProviderRequiredError",
    "ConfigurationError",
    "CRSPDataProviderAdapter",
    "YFinanceDataProviderAdapter",
    "UNIFIED_COLUMNS",
    "UNIFIED_SCHEMA",
]
