# ADR-016: Data Provider Protocol

## Status
Accepted

## Date
2025-12-05

## Context

The trading platform needs to access market data from multiple sources:

1. **yfinance:** Free data for development and testing
2. **CRSP:** Production-ready academic data from WRDS

These providers have different:
- **APIs:** yfinance uses ticker strings, CRSP uses PERMNO/GVKEY
- **Schemas:** yfinance has OHLCV, CRSP has prc/vol/ret
- **Capabilities:** CRSP has point-in-time universe, yfinance does not
- **Quality:** CRSP is survivorship-bias-free, yfinance is not

We need a unified interface that:
- Enables transparent provider switching via configuration
- Enforces production safety (CRSP required in production)
- Provides consistent output schema
- Supports environment-aware fallback

## Decision

We implement a **Protocol-based abstraction** with the **Adapter pattern**:

### 1. DataProvider Protocol

A `@runtime_checkable` protocol defining the common interface:

```python
@runtime_checkable
class DataProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def is_production_ready(self) -> bool: ...

    @property
    def supports_universe(self) -> bool: ...

    def get_daily_prices(
        self,
        symbols: list[str],
        start_date: date,
        end_date: date,
    ) -> pl.DataFrame: ...

    def get_universe(self, as_of_date: date) -> list[str]: ...
```

### 2. Adapter Implementations

Adapters wrap existing providers to conform to the protocol:

- `YFinanceDataProviderAdapter`: Wraps `YFinanceProvider`
- `CRSPDataProviderAdapter`: Wraps `CRSPLocalProvider`

Each adapter:
- Normalizes output to unified schema
- Implements capability flags (production_ready, supports_universe)
- Validates inputs and handles empty results

### 3. Unified Schema

Common columns across all providers with optional OHLC:

| Column | Type | Required | Notes |
|--------|------|----------|-------|
| date | Date | Yes | Trading date |
| symbol | String | Yes | Uppercase ticker |
| close | Float64 | Yes | Closing price (absolute) |
| volume | Float64 | Yes | Raw shares |
| ret | Float64 | Yes | Returns (null for yfinance) |
| open | Float64 | No | Null for CRSP |
| high | Float64 | No | Null for CRSP |
| low | Float64 | No | Null for CRSP |
| adj_close | Float64 | No | **Null for CRSP** |

**CRITICAL:** CRSP `adj_close` is NULL because CRSP `prc` is NOT split-adjusted.
Use `ret` for performance calculations with CRSP (ret IS split-adjusted).

### 4. UnifiedDataFetcher

Entry point with explicit provider selection rules:

- **AUTO mode:** CRSP preferred, fallback to yfinance in dev only
- **Production:** CRSP required, fallback disabled
- **Explicit:** Use specified provider, no fallback
- **Universe:** Always requires CRSP (yfinance raises error)

### 5. Custom Exceptions

Typed exceptions with useful attributes:

- `ProviderUnavailableError`: Provider not configured
- `ProviderNotSupportedError`: Operation not supported (e.g., universe on yfinance)
- `ProductionProviderRequiredError`: Production needs CRSP
- `ConfigurationError`: Invalid paths or config

## Alternatives Considered

### Alternative 1: Direct Integration (No Abstraction)

Call yfinance or CRSP directly based on environment.

**Rejected:**
- Code duplication in every caller
- No consistent schema
- Hard to test (mock each provider)
- Fallback logic scattered across codebase

### Alternative 2: Inheritance-Based Hierarchy

Abstract base class with concrete implementations.

**Rejected:**
- Python protocols are more flexible
- No need for shared state (adapters are stateless wrappers)
- Protocols work with `isinstance()` checks
- Easier to add new providers without modifying base class

### Alternative 3: Require OHLC from All Providers

Force all providers to provide open/high/low/adj_close.

**Rejected:**
- CRSP daily data doesn't include OHLC
- Would require WRDS schema changes or synthetic data
- Better to be explicit about nulls
- Consumers can handle null OHLC gracefully

### Alternative 4: Single DataFetcher with Provider Enum

One class with if/else based on provider type.

**Rejected:**
- Violates Open/Closed Principle
- Adding providers requires modifying existing code
- Harder to test individual providers
- Protocol + adapters is more extensible

## Consequences

### Positive

- **Transparent switching:** Change `DATA_PROVIDER` env var, code works unchanged
- **Type safety:** Protocol ensures adapters implement required methods
- **Testability:** Easy to mock adapters for unit tests
- **Extensibility:** New providers just need adapter + protocol compliance
- **Production safety:** Fallback forced off in production

### Negative

- **Extra layer:** Adapters add indirection (minimal performance impact)
- **Schema limitations:** Optional columns may cause null handling in consumers
- **Learning curve:** Developers must understand protocol/adapter pattern

### Risks

- **Schema drift:** Providers may add columns not in unified schema
  - *Mitigation:* Adapters select only unified columns
- **Provider changes:** yfinance/CRSP APIs may change
  - *Mitigation:* Changes isolated to adapters
- **Performance:** Additional wrapper layer
  - *Mitigation:* Adapters are thin wrappers, negligible overhead

## Implementation

### Files

- `libs/data_providers/protocols.py` - DataProvider protocol, adapters, exceptions
- `libs/data_providers/unified_fetcher.py` - UnifiedDataFetcher, FetcherConfig
- `scripts/fetch_data.py` - CLI script
- `tests/libs/data_providers/test_protocols.py` - Protocol tests
- `tests/libs/data_providers/test_unified_fetcher.py` - Fetcher tests

### Configuration

Environment variables:
- `DATA_PROVIDER`: auto | yfinance | crsp
- `ENVIRONMENT`: development | test | staging | production
- `YFINANCE_STORAGE_PATH`: Path to yfinance cache
- `CRSP_STORAGE_PATH`: Path to CRSP data
- `FALLBACK_ENABLED`: true | false (ignored in production)

## References

- [Unified Data Fetcher Concept](../CONCEPTS/unified-data-fetcher.md)
- [yfinance Limitations](../CONCEPTS/yfinance-limitations.md)
- [ADR-012: Local Data Warehouse](./ADR-012-local-data-warehouse.md)
