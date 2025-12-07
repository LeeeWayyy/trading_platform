# Unified Data Fetcher

**Status:** Production-ready
**Module:** `libs.data_providers.unified_fetcher`
**CLI:** `scripts/fetch_data.py`

---

## Overview

The Unified Data Fetcher provides a single entry point for market data access, abstracting the underlying data provider (yfinance for development, CRSP for production). It enables seamless switching between data sources via environment configuration while ensuring production safety.

---

## Key Benefits

- **Provider Abstraction:** Consistent API regardless of data source
- **Production Safety:** Automatic enforcement of CRSP in production
- **Unified Schema:** Common column structure across providers
- **Environment-Aware:** Auto-selection based on deployment environment
- **Fallback Support:** Development environments can fall back to yfinance

---

## Unified Schema

All data from the fetcher follows a consistent schema:

### Required Columns (Always Present)

| Column | Type | Description |
|--------|------|-------------|
| `date` | Date | Trading date |
| `symbol` | String | Ticker symbol (uppercase) |
| `close` | Float64 | Closing price (absolute) |
| `volume` | Float64 | Volume in raw shares |
| `ret` | Float64 | Holding period return (may be null) |

### Optional Columns (May Be Null)

| Column | Type | yfinance | CRSP |
|--------|------|----------|------|
| `open` | Float64 | Present | Null |
| `high` | Float64 | Present | Null |
| `low` | Float64 | Present | Null |
| `adj_close` | Float64 | Present | **Null** |

**CRITICAL:** CRSP `adj_close` is NULL because CRSP `prc` is NOT split-adjusted.
Use the `ret` column for performance calculations with CRSP data (ret IS split-adjusted).

---

## Provider Selection Rules

### AUTO Mode (Default)

| Environment | CRSP Available | Result |
|-------------|----------------|--------|
| Production | Yes | Use CRSP |
| Production | No | **ERROR** (ProductionProviderRequiredError) |
| Development/Test | Yes | Use CRSP |
| Development/Test | No | Fallback to yfinance (if enabled) |

### Explicit Mode

When `DATA_PROVIDER=yfinance` or `DATA_PROVIDER=crsp`:
- Uses specified provider
- **No fallback** (error if unavailable)
- Explicit selection overrides environment rules

### Universe Operations

The `get_universe()` operation **always requires CRSP**:
- yfinance has no concept of a tradeable universe
- Raises `ProviderNotSupportedError` if yfinance is selected

---

## Configuration

### Environment Variables

| Variable | Values | Default | Description |
|----------|--------|---------|-------------|
| `DATA_PROVIDER` | `auto`, `yfinance`, `crsp` | `auto` | Provider selection mode |
| `ENVIRONMENT` | `development`, `test`, `staging`, `production` | `development` | Deployment environment |
| `YFINANCE_STORAGE_PATH` | Path | None | yfinance cache directory |
| `CRSP_STORAGE_PATH` | Path | None | CRSP data directory |
| `MANIFEST_PATH` | Path | None | Data manifests directory |
| `FALLBACK_ENABLED` | `true`, `false` | `true` | Allow yfinance fallback (ignored in production) |

### Production Safety

**Fallback is ALWAYS disabled in production**, regardless of configuration:

```python
# These are equivalent in production:
config = FetcherConfig(environment="production", fallback_enabled=True)
config.fallback_enabled  # Always False in production
```

---

## Python API Usage

### Basic Usage

```python
from datetime import date
from libs.data_providers import (
    FetcherConfig,
    UnifiedDataFetcher,
    YFinanceProvider,
)

# Create config from environment
config = FetcherConfig.from_env()

# Initialize provider(s)
yf_provider = YFinanceProvider(storage_path=Path("data/yfinance"))

# Create fetcher
fetcher = UnifiedDataFetcher(
    config=config,
    yfinance_provider=yf_provider,
)

# Fetch daily prices
df = fetcher.get_daily_prices(
    symbols=["AAPL", "MSFT"],
    start_date=date(2024, 1, 1),
    end_date=date(2024, 12, 31),
)

# Check active provider
print(f"Using: {fetcher.get_active_provider()}")
```

### With CRSP for Production

```python
from libs.data_providers import CRSPLocalProvider

crsp_provider = CRSPLocalProvider(data_dir=Path("data/crsp"))

fetcher = UnifiedDataFetcher(
    config=config,
    yfinance_provider=yf_provider,  # Fallback for dev
    crsp_provider=crsp_provider,     # Primary for prod
)

# Get tradeable universe (requires CRSP)
symbols = fetcher.get_universe(as_of_date=date(2024, 1, 15))
```

---

## CLI Usage

The `scripts/fetch_data.py` CLI provides command-line access:

### Fetch Prices

```bash
# Print to stdout
python scripts/fetch_data.py prices --symbols AAPL,MSFT --start 2024-01-01 --end 2024-12-31

# Save to Parquet
python scripts/fetch_data.py prices --symbols AAPL --start 2024-01-01 --end 2024-12-31 --output prices.parquet

# Save to CSV
python scripts/fetch_data.py prices --symbols AAPL --start 2024-01-01 --end 2024-12-31 --output prices.csv

# Force specific provider
python scripts/fetch_data.py prices --symbols AAPL --start 2024-01-01 --end 2024-12-31 --provider crsp
```

### Get Universe

```bash
# Print symbols to stdout
python scripts/fetch_data.py universe --date 2024-01-15

# Save to file
python scripts/fetch_data.py universe --date 2024-01-15 --output symbols.txt
```

### Check Status

```bash
python scripts/fetch_data.py status
# Output:
# Environment: development
# Configured Provider: auto
# Active Provider: crsp
# Available Providers: crsp, yfinance
# Fallback Enabled: true
# CRSP Available: true
# yfinance Available: true
```

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | Provider error (unavailable, not supported) |
| 2 | Configuration error (invalid paths, missing config) |
| 3 | Data error (empty result, invalid symbols) |

---

## Error Handling

### Exception Types

| Exception | When Raised |
|-----------|-------------|
| `ProviderUnavailableError` | Requested provider not configured |
| `ProviderNotSupportedError` | Operation not supported (e.g., universe on yfinance) |
| `ProductionProviderRequiredError` | Production requires CRSP but unavailable |
| `ConfigurationError` | Invalid storage paths or config |
| `ValueError` | Empty symbols list |

### Example Error Handling

```python
from libs.data_providers import (
    ProductionProviderRequiredError,
    ProviderNotSupportedError,
    ProviderUnavailableError,
)

try:
    df = fetcher.get_daily_prices(symbols, start, end)
except ProviderUnavailableError as e:
    print(f"Provider '{e.provider_name}' not available")
    print(f"Available: {e.available_providers}")
except ProductionProviderRequiredError:
    print("Production requires CRSP - configure CRSP data source")
```

---

## See Also

- [ADR-016: Data Provider Protocol](../ADRs/ADR-016-data-provider-protocol.md) - Architecture decision
- [yfinance Limitations](./yfinance-limitations.md) - Why yfinance is dev-only
- [CRSP Data](./crsp-data.md) - CRSP data details
