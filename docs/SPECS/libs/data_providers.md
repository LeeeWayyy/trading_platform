# data_providers

## Identity
- **Type:** Library
- **Port:** N/A
- **Container:** N/A

## Interface
### Public Interface (Exported Classes & Functions)
| Class/Function | Parameters | Returns | Description |
|----------------|------------|---------|-------------|
| CRSPLocalProvider | storage_path, manifest_path | CRSPLocalProvider | Read-only CRSP access with DuckDB. |
| CompustatLocalProvider | storage_path, manifest_path | CompustatLocalProvider | Read-only Compustat access. |
| FamaFrenchLocalProvider | storage_path | FamaFrenchLocalProvider | Read-only factor data access. |
| YFinanceProvider | storage_path | YFinanceProvider | Dev-only market data provider. |
| UnifiedDataFetcher | config, yfinance_provider=None, crsp_provider=None | UnifiedDataFetcher | Provider-agnostic fetch interface. |
| FetcherConfig | provider, environment, paths, fallback_enabled | dataclass | Unified fetcher configuration. |
| ProviderType | Enum | type | CRSP, YFINANCE, AUTO. |
| DataProvider | Protocol | type | Unified provider interface. |
| CRSPDataProviderAdapter | crsp_provider | DataProvider | Adapter to protocol. |
| YFinanceDataProviderAdapter | yfinance_provider | DataProvider | Adapter to protocol. |
| WRDSClient | config | WRDSClient | WRDS connection wrapper. |
| WRDSConfig | - | dataclass | WRDS configuration. |
| SyncManager | - | SyncManager | Bulk data sync with progress tracking. |
| SyncProgress | - | dataclass | Sync progress output. |
| AtomicFileLock | path | AtomicFileLock | OS-level file lock. |
| atomic_lock | path | contextmanager | Lock context helper. |
| UNIFIED_COLUMNS | - | list[str] | Canonical unified schema columns. |
| UNIFIED_SCHEMA | - | dict | Schema map for validation. |
| ProviderUnavailableError | - | Exception | Provider missing. |
| ProviderNotSupportedError | - | Exception | Unsupported operation. |
| ProductionProviderRequiredError | - | Exception | Prod requires CRSP. |
| ConfigurationError | - | Exception | Invalid config. |
| DriftDetectedError | - | Exception | YFinance drift detection. |
| ProductionGateError | - | Exception | YFinance blocked in prod. |
| AmbiguousTickerError | - | Exception | CRSP ticker ambiguity. |
| AmbiguousGVKEYError | - | Exception | Compustat GVKEY ambiguity. |
| ManifestVersionChangedError | - | Exception | CRSP manifest mismatch. |
| CompustatManifestVersionChangedError | - | Exception | Compustat manifest mismatch. |
| FamaFrenchSyncError | - | Exception | Fama-French sync error. |
| ChecksumError | - | Exception | Fama-French checksum error. |
| LockAcquisitionError | - | Exception | Lock failure. |
| LockRecoveryError | - | Exception | Lock recovery failure. |
| MalformedLockFileError | - | Exception | Invalid lock file. |

## Behavioral Contracts

### Key Functions (detailed behavior)
#### UnifiedDataFetcher.get_* / get_daily_prices(...)
**Purpose:** Provide a single entry point for market data across providers.

**Preconditions:**
- At least one provider adapter is configured.

**Postconditions:**
- Returns data in unified schema (UNIFIED_COLUMNS).

**Behavior:**
1. Select provider based on config and environment rules.
2. Enforce production rules: CRSP required in production AUTO mode.
3. Fetch data and normalize to unified schema.

**Raises:**
- `ProviderUnavailableError`, `ProviderNotSupportedError`, `ProductionProviderRequiredError`.

#### DataProvider protocol
**Purpose:** Standardize provider interface for price/universe access.

**Behavior:**
- Implementations must be thread-safe and return unified schema columns.

#### AtomicFileLock
**Purpose:** Ensure single-writer semantics for sync operations.

**Behavior:**
- Uses OS-level atomic file creation and lock files.

### Invariants
- Unified schema columns are present in all provider outputs.
- YFinance is blocked for production usage.

### State Machine (if stateful)
N/A

## Data Flow
```
Provider (CRSP/YFinance) -> Adapter -> UnifiedDataFetcher -> polars DataFrame
```
- **Input format:** symbols list + date range.
- **Output format:** polars DataFrame with UNIFIED_COLUMNS.
- **Side effects:** Optional sync writes and lock files.

## Usage Examples
### Example 1: Unified fetch
```python
config = FetcherConfig(environment="development")
fetcher = UnifiedDataFetcher(config, yfinance_provider=yf)
prices = fetcher.get_daily_prices(symbols=["AAPL"], start_date=start, end_date=end)
```

### Example 2: Provider protocol
```python
provider: DataProvider = CRSPDataProviderAdapter(crsp_provider)
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Production AUTO without CRSP | env=production | Raises ProductionProviderRequiredError. |
| Universe request on yfinance | get_universe() | Raises ProviderNotSupportedError. |
| Lock acquisition fails | concurrent sync | Raises LockAcquisitionError. |

## Dependencies
- **Internal:** libs/data_quality, libs/data_pipeline
- **External:** polars, duckdb

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| DATA_PROVIDER | No | AUTO | Provider selection (AUTO/CRSP/YFINANCE). |
| FALLBACK_ENABLED | No | true | Allow fallback in non-prod. |
| YFINANCE_STORAGE_PATH | No | N/A | YFinance storage path. |
| CRSP_STORAGE_PATH | No | N/A | CRSP storage path. |
| MANIFEST_PATH | No | N/A | Manifest path override. |

## Error Handling
- Provider selection and availability errors raise typed exceptions.
- Sync/locking errors raise lock exceptions.

## Observability (Services only)
### Health Check
N/A

### Metrics
N/A

## Security
- **Auth Required:** N/A
- **Auth Method:** N/A
- **Data Sensitivity:** Internal
- **RBAC Roles:** N/A

## Testing
- **Test Files:** `tests/libs/data_providers/`
- **Run Tests:** `pytest tests/libs/data_providers -v`
- **Coverage:** N/A

## Related Specs
- `../libs/data_pipeline.md`
- `../libs/alpha.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-03
- **Source Files:** `libs/data_providers/__init__.py`, `libs/data_providers/unified_fetcher.py`, `libs/data_providers/protocols.py`, `libs/data_providers/crsp_local_provider.py`, `libs/data_providers/compustat_local_provider.py`, `libs/data_providers/fama_french_local_provider.py`, `libs/data_providers/yfinance_provider.py`, `libs/data_providers/locking.py`, `libs/data_providers/sync_manager.py`, `libs/data_providers/wrds_client.py`
- **ADRs:** `docs/ADRs/ADR-016-data-provider-protocol.md`
