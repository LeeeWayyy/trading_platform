# Data Directory

Market data storage, processing outputs, and schema documentation for the trading platform.

---

## Overview

This directory contains **local data files** organized by data type and processing stage:
- **Raw market data** (OHLCV, adjusted prices)
- **Backtest results** (portfolio returns, IC metrics)
- **Data quality monitoring** (quarantine, validation manifests)
- **Sync progress** (incremental download tracking)

**Storage format:** Parquet (columnar, compressed, type-safe)
**Catalog:** DuckDB for local querying (see `libs/data/duckdb_catalog`)

---

## Directory Structure

```
data/
├── README.md              # This file
├── adjusted/              # Adjusted OHLCV data (split/dividend adjusted)
│   └── YYYY-MM-DD/        # Date-partitioned
│       └── {SYMBOL}.parquet
├── backtest_results/      # Backtest outputs (portfolio metrics, IC, factor performance)
│   └── {job_id}/
│       ├── daily_ic.parquet
│       ├── daily_portfolio_returns.parquet
│       ├── factor_performance.parquet
│       └── metadata.json
├── backtests/             # Backtest configurations (not results)
├── manifests/             # Data quality manifests (checksum, row counts)
├── quarantine/            # Data rejected by quality checks
│   └── YYYY-MM-DD/
│       └── {SYMBOL}.parquet
├── sync_progress/         # Incremental sync tracking
├── tmp/                   # Temporary processing files (gitignored)
└── yfinance/              # yfinance cache (gitignored)
```

**Partitioning strategy:** Date-based partitioning for time-series data (`YYYY-MM-DD/` subdirectories)

---

## Data Schemas

### 1. Adjusted OHLCV Data (`adjusted/`)

**Schema:**
```python
{
    "symbol": str,           # Ticker symbol (e.g., "AAPL")
    "date": datetime,        # Trading date (UTC midnight)
    "open": float,           # Adjusted open price
    "high": float,           # Adjusted high price
    "low": float,            # Adjusted low price
    "close": float,          # Adjusted close price
    "volume": int64,         # Trading volume
    "dividends": float,      # Dividend amount (0 if no dividend)
    "stock_splits": float,   # Split ratio (1.0 if no split)
    "adj_close": float,      # Fully adjusted close (for validation)
}
```

**Validation rules:**
- `open`, `high`, `low`, `close` > 0
- `high` >= max(`open`, `close`)
- `low` <= min(`open`, `close`)
- `volume` >= 0
- No null values in price fields
- Dates must be trading days (NYSE calendar)

**File naming:** `{SYMBOL}.parquet` (e.g., `AAPL.parquet`)
**Partition:** `adjusted/YYYY-MM-DD/`

**Example:**
```python
import polars as pl

# Read single symbol for date
df = pl.read_parquet("data/adjusted/2026-01-14/AAPL.parquet")

# Query multiple symbols
df = pl.concat([
    pl.read_parquet(f"data/adjusted/2026-01-14/{sym}.parquet")
    for sym in ["AAPL", "MSFT", "GOOGL"]
])
```

---

### 2. Backtest Results (`backtest_results/`)

**Job structure:** `backtest_results/{job_id}/`

Each backtest job produces:

#### a. Daily Portfolio Returns (`daily_portfolio_returns.parquet`)
```python
{
    "date": datetime,        # Trading date
    "portfolio_return": float,  # Daily portfolio return (%)
    "benchmark_return": float,  # Benchmark return (e.g., SPY)
    "alpha": float,          # Alpha over benchmark
    "sharpe": float,         # Rolling sharpe ratio
    "max_drawdown": float,   # Max drawdown to date
    "positions_count": int,  # Number of positions held
}
```

#### b. Daily IC (Information Coefficient) (`daily_ic.parquet`)
```python
{
    "date": datetime,        # Prediction date
    "ic_spearman": float,    # Spearman IC (rank correlation)
    "ic_pearson": float,     # Pearson IC (linear correlation)
    "ic_sign_ratio": float,  # % of predictions with correct sign
    "n_stocks": int,         # Number of stocks predicted
}
```

#### c. Factor Performance (`factor_performance.parquet`)
```python
{
    "factor_name": str,      # Factor identifier (e.g., "RSI_14")
    "mean_ic": float,        # Mean IC over backtest period
    "ic_ir": float,          # IC information ratio
    "coverage": float,       # % of dates with IC calculated
    "sharpe": float,         # Factor sharpe ratio
}
```

#### d. Metadata (`metadata.json`)
```json
{
  "job_id": "jid_abc123",
  "strategy": "alpha_baseline",
  "start_date": "2023-01-01",
  "end_date": "2023-12-31",
  "symbols": ["AAPL", "MSFT", ...],
  "model_hash": "sha256:...",
  "created_at": "2026-01-14T10:00:00Z",
  "parameters": {...}
}
```

---

### 3. Quarantine Data (`quarantine/`)

**Purpose:** Store data that failed quality checks for investigation.

**Schema:** Same as `adjusted/` but with additional metadata column:
```python
{
    ...  # Same as adjusted/ schema
    "quarantine_reason": str,  # Validation failure reason
    "quarantine_timestamp": datetime,  # When quarantined
}
```

**Common quarantine reasons:**
- `"negative_price"`: Price <= 0
- `"invalid_ohlc"`: High < Low or violates OHLC rules
- `"excessive_gap"`: >50% price change day-over-day (potential data error)
- `"missing_volume"`: Volume = 0 on trading day
- `"duplicate_date"`: Multiple records for same date

**Recovery process:**
1. Investigate quarantine reason
2. Validate data source
3. Re-download if source error
4. Move back to `adjusted/` if false positive

---

### 4. Manifests (`manifests/`)

**Purpose:** Track data quality metrics and checksums.

**Schema (`data_manifest.parquet`):**
```python
{
    "symbol": str,           # Ticker symbol
    "date": datetime,        # Data date
    "file_path": str,        # Relative path to file
    "row_count": int,        # Number of rows
    "checksum": str,         # SHA256 of file
    "created_at": datetime,  # Manifest creation time
    "validation_status": str,  # "passed" | "quarantined"
}
```

**Usage:**
```python
# Check which symbols have data for date
manifest = pl.read_parquet("data/manifests/data_manifest.parquet")
available = manifest.filter(pl.col("date") == "2026-01-14")["symbol"].to_list()
```

---

### 5. Sync Progress (`sync_progress/`)

**Purpose:** Track incremental data sync state (WRDS, yfinance).

**Schema (`sync_state.json`):**
```json
{
  "source": "yfinance",
  "last_sync": "2026-01-14T08:00:00Z",
  "symbols": {
    "AAPL": {
      "last_date": "2026-01-13",
      "status": "success"
    },
    "MSFT": {
      "last_date": "2026-01-13",
      "status": "success"
    }
  }
}
```

---

## Schema Versioning

**Current version:** `v1.0` (as of 2026-01-14)

**Version history:**
- **v1.0** (2026-01-14): Initial schema with adjusted OHLCV + backtest results

**Future versions:** If schema changes, use versioned subdirectories:
```
data/
├── adjusted_v1/   # Old schema
├── adjusted_v2/   # New schema
└── ...
```

**Breaking change process:**
1. Create ADR documenting schema change
2. Create new versioned directory
3. Migrate existing data (run migration script)
4. Update downstream code to use new schema
5. Deprecate old schema after 30 days

---

## Data Quality Checks

**Automated validation** (via `libs/data/data_quality/validators.py`):

| Check | Severity | Action |
|-------|----------|--------|
| Negative prices | CRITICAL | Quarantine |
| Invalid OHLC | HIGH | Quarantine |
| Missing volume | MEDIUM | Warn + accept |
| Excessive gap (>50%) | HIGH | Quarantine |
| Duplicate dates | CRITICAL | Quarantine |
| Stale data (>7 days) | LOW | Warn |

**Manual review:**
```bash
# Check quarantine directory
ls -la data/quarantine/YYYY-MM-DD/

# Inspect quarantined file
python scripts/dev/inspect_quarantine.py --date 2026-01-14 --symbol AAPL
```

---

## Data Lifecycle

### 1. Ingestion
```
Source (yfinance/WRDS) → Raw fetch → Validation → adjusted/ or quarantine/
```

### 2. Processing
```
adjusted/ → Feature engineering → Model training → Predictions
```

### 3. Backtest
```
adjusted/ + Predictions → Backtest engine → backtest_results/
```

### 4. Archival
**Policy:** Keep adjusted data indefinitely (compressed)
**Cleanup:** Remove tmp/ files older than 7 days

---

## Downstream Consumers

**Services that read from data/:**
- `apps/signal_service` - Reads adjusted/ for feature generation
- `apps/orchestrator` - Reads backtest_results/ for model evaluation
- `libs/data/market_data` - Reads adjusted/ for historical data
- `libs/backtest` - Writes to backtest_results/

**Schema contract:** All consumers must handle missing data gracefully (forward-fill, skip, or error with clear message).

---

## Common Operations

### Add New Data Type

1. Create subdirectory: `data/my_data_type/`
2. Document schema in this README
3. Add validation logic in `libs/data/data_quality/`
4. Update manifest schema if needed
5. Add tests in `tests/libs/data/`

### Migrate Schema

1. Create ADR: `docs/ADRs/XXXX-data-schema-v2.md`
2. Create migration script: `scripts/data/migrate_v1_to_v2.py`
3. Run migration (dry-run first)
4. Update code to use new schema
5. Deprecate old schema

### Validate Data Integrity

```bash
# Check manifests for missing data
python scripts/dev/validate_data_integrity.py --date 2026-01-14

# Verify checksums
python scripts/dev/verify_checksums.py --path data/adjusted/2026-01-14/
```

---

## Performance Considerations

**Parquet benefits:**
- **Columnar:** Read only needed columns (faster queries)
- **Compressed:** ~10x compression vs CSV (saves disk space)
- **Type-safe:** Schema enforced at write time

**DuckDB integration:**
```python
import duckdb

# Query parquet directly (no load to memory)
df = duckdb.query("""
    SELECT symbol, close, volume
    FROM 'data/adjusted/2026-01-*/*.parquet'
    WHERE symbol IN ('AAPL', 'MSFT')
    ORDER BY date
""").pl()
```

**Partitioning strategy:**
- Date partitioning reduces scan size (only read needed dates)
- Symbol-level files allow parallel processing

---

## Security & Access Control

**Sensitive data:** None (public market data only)

**Access control:**
- Development: Read/write access for all developers
- Production: Read-only for services (write via data pipeline only)

**Backup:**
- Adjusted data backed up daily to S3 (not implemented yet)
- Backtest results retained for 90 days (configurable)

---

## Troubleshooting

### Issue: Missing data for symbol/date

**Diagnosis:**
```python
import polars as pl

# Check manifest
manifest = pl.read_parquet("data/manifests/data_manifest.parquet")
status = manifest.filter(
    (pl.col("symbol") == "AAPL") &
    (pl.col("date") == "2026-01-14")
)
print(status)
```

**Fix:** Re-run sync script:
```bash
python scripts/data/taq_sync.py --symbol AAPL --date 2026-01-14
```

### Issue: Data in quarantine

**Diagnosis:**
```bash
ls data/quarantine/2026-01-14/
```

**Fix:** Investigate quarantine reason, validate source, re-download if needed.

### Issue: Schema mismatch error

**Error:** `polars.exceptions.SchemaError: expected column 'dividends' but not found`

**Fix:** Re-export data with correct schema:
```bash
python scripts/data/reexport_with_schema.py --date 2026-01-14
```

---

## Related Documentation

- [Data Quality Standards](../docs/STANDARDS/DATA_QUALITY.md) - Validation rules and thresholds
- [DuckDB Catalog](../libs/data/duckdb_catalog/README.md) - Query interface
- [Market Data Service Spec](../docs/SPECS/services/market_data_service.md) - API for data access
- [Backtest Service Spec](../docs/SPECS/libs/backtest.md) - Backtest result schema

---

**Last Updated:** 2026-01-14
**Schema Version:** v1.0
**Maintained By:** Data Engineering Team
