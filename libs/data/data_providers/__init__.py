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

import logging as _logging

from libs.data.data_providers.compustat_local_provider import (
    AmbiguousGVKEYError,
    CompustatLocalProvider,
)
from libs.data.data_providers.compustat_local_provider import (
    ManifestVersionChangedError as CompustatManifestVersionChangedError,
)
from libs.data.data_providers.crsp_local_provider import (
    AmbiguousTickerError,
    CRSPLocalProvider,
    ManifestVersionChangedError,
)
from libs.data.data_providers.fama_french_local_provider import (
    ChecksumError,
    FamaFrenchLocalProvider,
    FamaFrenchSyncError,
)
from libs.data.data_providers.locking import (
    AtomicFileLock,
    LockAcquisitionError,
    LockRecoveryError,
    MalformedLockFileError,
    atomic_lock,
)

# Unified Data Fetcher (P4T1.8)
from libs.data.data_providers.protocols import (
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
from libs.data.data_providers.unified_fetcher import (
    FetcherConfig,
    ProviderType,
    UnifiedDataFetcher,
)
from libs.data.data_providers.universe import (
    CRSPUnavailableError,
    ForwardReturnsProvider,
    UniverseProvider,
)
from libs.data.data_providers.yfinance_provider import (
    DriftDetectedError,
    ProductionGateError,
    YFinanceError,
    YFinanceProvider,
)

_dp_logger = _logging.getLogger(__name__)

# Known optional third-party packages that data-provider submodules may
# depend on.  If these are absent we degrade gracefully; any *other*
# missing module is a genuine regression and must fail fast.
_OPTIONAL_PACKAGES = frozenset({
    "wrds",       # WRDS database driver
    "sas7bdat",   # SAS file reader
    "saspy",      # SAS scripting
    "paramiko",   # SSH transport for WRDS
})


def _is_optional_dep(exc: ModuleNotFoundError) -> bool:
    """Return True when *exc* is caused by a known optional package."""
    return exc.name is not None and any(
        exc.name == pkg or exc.name.startswith(f"{pkg}.")
        for pkg in _OPTIONAL_PACKAGES
    )


try:
    from libs.data.data_providers.sync_manager import SyncManager, SyncProgress
except ModuleNotFoundError as _exc:
    if _exc.name == "libs.data.data_providers.sync_manager" or _is_optional_dep(_exc):
        _dp_logger.info(
            "sync_manager_unavailable: %s (missing_package=%s)", _exc, _exc.name
        )
        SyncManager = None  # type: ignore[assignment,misc]
        SyncProgress = None  # type: ignore[assignment,misc]
    else:
        raise

try:
    from libs.data.data_providers.wrds_client import WRDSClient, WRDSConfig
except ModuleNotFoundError as _exc:
    if _exc.name == "libs.data.data_providers.wrds_client" or _is_optional_dep(_exc):
        _dp_logger.info(
            "wrds_client_unavailable: %s (missing_package=%s)", _exc, _exc.name
        )
        WRDSClient = None  # type: ignore[assignment,misc]
        WRDSConfig = None  # type: ignore[assignment,misc]
    else:
        raise

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
    # Universe and Forward Returns (P6T10)
    "UniverseProvider",
    "ForwardReturnsProvider",
    "CRSPUnavailableError",
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
