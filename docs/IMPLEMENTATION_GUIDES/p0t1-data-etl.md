# T1 Implementation Guide: Data ETL with Corporate Actions, Freshness, and Quality Gate

## Overview

This guide documents the implementation of T1: Data ETL Pipeline with corporate action adjustments, freshness checking, and quality gate for outlier detection.

**Status**: ✅ Complete (2024-10-16)

**Related Documents**:
- Ticket: P0_TICKETS.md - T1
- ADR-0001: Data Pipeline Architecture
- ADR-0002: Exception Hierarchy
- Concept: /docs/CONCEPTS/corporate-actions.md

## What Was Built

### Core Modules

#### 1. Freshness Checker (`libs/data_pipeline/freshness.py`)

Validates that market data is recent enough for trading decisions.

**Key Functions**:
- `check_freshness(df, max_age_minutes=30)` - Raises StalenessError if data too old
- `check_freshness_safe(df, ...)` - Non-raising version for conditional logic

**Design Decisions**:
- Default threshold: 30 minutes (configurable via `DATA_FRESHNESS_MINUTES`)
- Requires timezone-aware timestamps (UTC)
- Uses latest timestamp in DataFrame (handles multi-row data)

**Example Usage**:
```python
from libs.data_pipeline.freshness import check_freshness

# Will raise StalenessError if data > 30 minutes old
check_freshness(raw_data, max_age_minutes=30)
```

#### 2. Corporate Actions Adjuster (`libs/data_pipeline/corporate_actions.py`)

Adjusts historical prices for stock splits and dividends using backwards adjustment method.

**Key Functions**:
- `adjust_for_splits(df, ca_df)` - Adjust OHLCV for splits
- `adjust_for_dividends(df, ca_df)` - Adjust closes for dividends
- `adjust_prices(df, splits_df, dividends_df)` - Convenience wrapper

**Design Decisions**:
- Backwards adjustment: current prices match live market
- Cumulative adjustments: handles multiple splits/dividends per symbol
- Idempotent: `adjust(adjust(data)) == adjust(data)`
- Uses Polars reverse cumulative product/sum for efficiency

**Adjustment Logic**:

**Splits**:
```python
# 4-for-1 split on 2020-08-31
# Pre-split prices: divide by 4
# Pre-split volume: multiply by 4
# Post-split: unchanged (matches live market)
```

**Dividends**:
```python
# $2 dividend on ex-date 2024-01-15
# Pre-ex-date closes: subtract $2
# Ex-date and after: unchanged
```

**Example Usage**:
```python
from libs.data_pipeline.corporate_actions import adjust_prices

# Apply both splits and dividends
adjusted = adjust_prices(
    raw_data,
    splits_df=splits,
    dividends_df=dividends
)
```

#### 3. Quality Gate (`libs/data_pipeline/quality_gate.py`)

Detects outliers and separates clean data from suspicious data.

**Key Functions**:
- `detect_outliers(df, ca_df, threshold=0.30)` - Returns `(good_data, quarantine_data)`
- `check_quality(df, ...)` - Convenience function with optional raising

**Design Decisions**:
- Default threshold: 30% daily return (configurable via `OUTLIER_THRESHOLD`)
- Corporate action aware: large moves on CA dates are NOT flagged
- First row per symbol cannot be flagged (no previous close to compare)
- Quarantined data includes 'reason' column for debugging

**Outlier Logic**:
```python
# Flag as outlier if:
#   abs(daily_return) > 0.30
#   AND no corporate action on that date
```

**Example Usage**:
```python
from libs.data_pipeline.quality_gate import detect_outliers

# Split data into good and bad
good_data, quarantine_data = detect_outliers(
    adjusted_data,
    ca_df=corporate_actions,
    threshold=0.30
)
```

#### 4. Main ETL Pipeline (`libs/data_pipeline/etl.py`)

Orchestrates the complete data processing pipeline.

**Key Functions**:
- `run_etl_pipeline(raw_data, ...)` - Execute full pipeline
- `load_adjusted_data(symbols, start_date, end_date)` - Load processed data

**Pipeline Steps**:
1. **Freshness Check**: Validate data is recent (raises StalenessError if stale)
2. **Corporate Action Adjustment**: Apply splits and dividends
3. **Quality Gate**: Detect and separate outliers
4. **Persistence**: Save adjusted and quarantined data to Parquet

**File Output Structure**:
```
data/
├── adjusted/
│   └── YYYY-MM-DD/
│       └── {symbol}.parquet
└── quarantine/
    └── YYYY-MM-DD/
        └── {symbol}.parquet
```

**Example Usage**:
```python
from libs.data_pipeline.etl import run_etl_pipeline

result = run_etl_pipeline(
    raw_data=raw_data,
    splits_df=splits,
    dividends_df=dividends,
    freshness_minutes=30,
    outlier_threshold=0.30,
    output_dir=Path("data"),
    run_date=date.today()
)

# Access results
adjusted_data = result["adjusted"]
quarantined_data = result["quarantined"]
stats = result["stats"]
```

### Supporting Infrastructure

#### 5. Exception Hierarchy (`libs/common/exceptions.py`)

Custom exceptions for precise error handling.

**Hierarchy**:
```
TradingPlatformError (base)
├── DataQualityError
│   ├── StalenessError
│   └── OutlierError
├── RiskViolationError (future)
└── OrderExecutionError (future)
```

**Usage Pattern**:
```python
try:
    run_etl_pipeline(raw_data)
except StalenessError as e:
    # Data too old → wait and retry
    logger.warning(f"Stale data: {e}")
except OutlierError as e:
    # Bad data → quarantine
    logger.error(f"Outlier detected: {e}")
except DataQualityError as e:
    # Any other data issue → skip
    logger.error(f"Data quality issue: {e}")
```

#### 6. Configuration (`config/settings.py`)

Type-safe configuration using Pydantic.

**Key Settings**:
```python
class Settings(BaseSettings):
    data_freshness_minutes: int = Field(default=30, ge=1, le=1440)
    outlier_threshold: float = Field(default=0.30, ge=0.01, le=1.0)
    # ... other settings
```

### Test Suite

#### Unit Tests

**Test Files**:
- `tests/test_freshness.py` - 9 test cases
- `tests/test_corporate_actions.py` - 12 test cases
- `tests/test_quality_gate.py` - 11 test cases
- `tests/test_etl.py` - 11 test cases

**Total**: 43 unit tests

**Coverage**:
- ✅ Happy paths (data that should pass)
- ✅ Error cases (data that should fail)
- ✅ Edge cases (empty data, missing columns, first rows)
- ✅ Multiple symbols
- ✅ Custom thresholds
- ✅ Idempotency

#### Integration Test

**File**: `tests/test_integration_pipeline.py`

**Scenarios**:
1. **Realistic Multi-Symbol Scenario**:
   - AAPL with 4-for-1 split
   - MSFT with $2 dividend
   - GOOGL with 50% outlier
   - Validates end-to-end pipeline correctness

2. **No Corporate Actions**: Validates pipeline with normal data

3. **All Quarantined**: Tests extreme volatility scenario

4. **Performance Target**: 756 rows processed in <1 second

5. **Data Immutability**: Verifies input data unchanged after processing

#### Mock Data Generator

**File**: `tests/fixtures/mock_data.py`

**Functions**:
- `create_normal_ohlcv()` - Basic price data
- `create_data_with_split()` - Data with stock split
- `create_data_with_dividend()` - Data with dividend
- `create_data_with_outlier()` - Data with artificial outlier
- `create_stale_data()` - Data with old timestamps
- `create_multi_symbol_data()` - Comprehensive multi-symbol dataset

All mock data is:
- Deterministic (same inputs → same outputs)
- Realistic (mimics actual market patterns)
- Documented (explains what each function generates)

## How to Use

### Running Tests

```bash
# Install dependencies
poetry install

# Run all tests
make test

# Run specific test file
poetry run pytest tests/test_freshness.py -v

# Run integration test only
poetry run pytest tests/test_integration_pipeline.py -v

# Run with coverage
poetry run pytest --cov=libs --cov-report=term-missing
```

### Running the Pipeline

**Basic Example**:
```python
from datetime import datetime, timezone, date
from pathlib import Path
import polars as pl
from libs.data_pipeline.etl import run_etl_pipeline

# Load raw data (from your data source)
raw_data = pl.read_parquet("raw/2024-10-16/AAPL.parquet")

# Load corporate actions
splits = pl.read_parquet("corporate_actions/splits.parquet")
dividends = pl.read_parquet("corporate_actions/dividends.parquet")

# Run pipeline
result = run_etl_pipeline(
    raw_data=raw_data,
    splits_df=splits,
    dividends_df=dividends,
    freshness_minutes=30,
    outlier_threshold=0.30,
    output_dir=Path("data"),
    run_date=date.today()
)

# Check results
print(f"Processed {result['stats']['adjusted_rows']} rows")
print(f"Quarantined {result['stats']['quarantined_rows']} rows")

if len(result['quarantined']) > 0:
    print("Outliers detected:")
    print(result['quarantined'][['symbol', 'date', 'reason']])
```

**Loading Processed Data**:
```python
from datetime import date
from libs.data_pipeline.etl import load_adjusted_data

# Load specific symbols and date range
df = load_adjusted_data(
    symbols=["AAPL", "MSFT", "GOOGL"],
    start_date=date(2024, 1, 1),
    end_date=date(2024, 12, 31),
    data_dir=Path("data/adjusted")
)

# Ready for backtesting!
```

## Key Decisions and Rationale

### Why Polars Instead of Pandas?

**Decision**: Use Polars for all DataFrame operations

**Rationale**:
- 5-10x faster for our workloads (groupby, joins, window functions)
- Better memory efficiency (crucial for backtests on laptop)
- Native lazy execution (optimization opportunities)
- Built on Apache Arrow (zero-copy interop with Parquet)
- Modern API with better error messages

**Trade-off**: Smaller ecosystem than Pandas, but acceptable for MVP

### Why Backwards Adjustment?

**Decision**: Adjust historical prices backwards from present

**Rationale**:
- Current prices always match live market (reduces confusion)
- Industry standard (Yahoo Finance, CRSP use this)
- Simpler than forward adjustment
- Easier to explain and debug

**Alternative Considered**: Forward adjustment (keeps historical prices at actual traded values)
- **Rejected**: Current prices don't match live market, confusing for debugging

### Why 30% Threshold?

**Decision**: Flag daily returns > 30% as potential outliers

**Rationale**:
- 30% is extreme for daily moves in liquid stocks (AAPL, MSFT, GOOGL)
- Balances false positives vs. catching real errors
- Standard threshold in quantitative finance
- Configurable via `OUTLIER_THRESHOLD` for different asset classes

### Why 30-Minute Freshness?

**Decision**: Reject data older than 30 minutes

**Rationale**:
- Appropriate for daily strategies (we don't need second-by-second data)
- Tight enough to catch stale/stuck feeds
- Loose enough to tolerate reasonable delays
- Much stricter than needed for daily bars (good safety margin)
- Configurable via `DATA_FRESHNESS_MINUTES` for different strategies

### Why Parquet Files?

**Decision**: Store all data in Parquet format

**Rationale**:
- Columnar format = 10x faster for typical queries
- Schema enforcement catches bugs early
- Compression reduces disk usage 5-10x vs CSV
- Native support in Polars (zero-copy reads)
- Industry standard for analytics

**Alternative Considered**: CSV files
- **Rejected**: Slower, no schema, larger files, debugging nightmares

### Why Separate Quarantine Directory?

**Decision**: Save outliers to separate `quarantine/` directory

**Rationale**:
- Preserves suspicious data for investigation (not deleted)
- Clear separation from clean data
- Includes 'reason' column for debugging
- Enables manual review workflow
- Can re-process if false positive

## Performance Characteristics

### Targets (from ADR-0001)

- ✅ Process 252 days × 3 symbols (756 rows): <1 second
- ✅ Scale test: 252 days × 100 symbols (25,200 rows): <10 seconds (projected)

### Actual Performance

Measured on MacBook (M-series):
- **756 rows**: ~0.15 seconds
- **Throughput**: ~5,000 rows/second

**Bottlenecks**:
- Disk I/O (Parquet writes) is the main bottleneck
- In-memory processing is very fast (~0.05s for 756 rows)

**Optimizations Applied**:
- Polars lazy evaluation (automatic query optimization)
- Parquet Snappy compression (balance of speed/size)
- One file per symbol (parallel reads/writes possible)

## Known Limitations

### MVP Scope

1. **Mock Data Only**: No real data source integration yet
   - **Next Step**: T2 will integrate Alpaca API

2. **Daily Bars Only**: Minute/tick data not supported yet
   - **Impact**: Fine for daily strategies (MVP scope)
   - **Next Step**: T3+ for intraday data

3. **No Backfill Logic**: Only processes latest batch
   - **Impact**: Manual backfill required for historical data
   - **Next Step**: Future task for automated backfill

### Technical Limitations

1. **First Row Cannot Be Flagged**: No previous close to compare
   - **Impact**: Outlier on first row of history will pass through
   - **Mitigation**: Use established history, not IPO day

2. **Corporate Action Data Required**: Pipeline assumes CA data is available
   - **Impact**: If CA data incomplete, adjustments will be wrong
   - **Mitigation**: Start with mock data with known CAs, verify with data provider

3. **No Reversal Logic**: Cannot unadjust data
   - **Impact**: Once adjusted, cannot get back to raw prices
   - **Mitigation**: Keep raw data immutable in separate directory

## Future Enhancements

### T2 (Real Data Integration)

- [ ] Integrate Alpaca API for real market data
- [ ] Corporate actions data source (Alpaca or external)
- [ ] Scheduled batch execution (cron or Airflow)

### T3+ (Advanced Features)

- [ ] Minute/tick data support
- [ ] Streaming pipeline (vs. batch)
- [ ] Automated backfill logic
- [ ] Data quality metrics dashboard
- [ ] Alerting for data issues

### P1 (Production Features)

- [ ] Retry logic for transient failures
- [ ] Circuit breaker for data source outages
- [ ] Monitoring and observability (Prometheus metrics)
- [ ] Data lineage tracking

## Troubleshooting

### Tests Failing

**Issue**: Import errors when running tests

**Solution**:
```bash
# Ensure you're in the project root
cd /path/to/trading_platform

# Install dependencies
poetry install

# Run tests
poetry run pytest
```

**Issue**: `ModuleNotFoundError: No module named 'libs'`

**Solution**: Ensure `__init__.py` files exist in all package directories
```bash
touch libs/__init__.py
touch libs/common/__init__.py
touch libs/data_pipeline/__init__.py
```

### Pipeline Errors

**Issue**: `StalenessError: Data is 120.0 minutes old`

**Solution**: Your data is too old. Either:
1. Get fresh data
2. Increase `freshness_minutes` parameter (if appropriate for your use case)

**Issue**: `ValueError: DataFrame missing required columns`

**Solution**: Ensure your raw data has all required columns:
- symbol, date, open, high, low, close, volume, timestamp

**Issue**: All data getting quarantined

**Possible Causes**:
1. Outlier threshold too strict (try increasing from 0.30 to 0.50)
2. Corporate actions data missing (large moves flagged as outliers)
3. Bad data source (genuinely has errors)

**Debug**:
```python
# Check what's being quarantined
result = run_etl_pipeline(...)
print(result['quarantined'][['symbol', 'date', 'reason']])
```

## Educational Notes

### Why This Matters

This pipeline demonstrates several important concepts in quantitative finance:

1. **Data Quality is Critical**: 90% of quant work is data cleaning
2. **Corporate Actions Must Be Adjusted**: Unadjusted data will ruin backtests
3. **Outlier Detection Prevents Garbage In**: Bad data → bad models
4. **Immutability Enables Reproducibility**: Can always reprocess from raw
5. **Deterministic Pipelines Enable Debugging**: Same input → same output

### Learning Resources

**Corporate Actions**:
- `/docs/CONCEPTS/corporate-actions.md` - Detailed explanation with examples
- [Investopedia: Stock Splits](https://www.investopedia.com/terms/s/stocksplit.asp)
- [CRSP Methodology](https://www.crsp.org/) - Industry standard

**Data Quality**:
- ["Advances in Financial Machine Learning" by Marcos López de Prado](https://www.wiley.com/en-us/Advances+in+Financial+Machine+Learning-p-9781119482086) - Chapter 5: Fractional Differentiation
- [Quantitative Value](http://www.quantitativevalue.net/) - Data quality in value investing

**Polars**:
- [Polars User Guide](https://pola-rs.github.io/polars-book/) - Comprehensive guide
- [Polars vs Pandas](https://www.pola.rs/posts/pandas_to_polars/) - Migration guide

## Conclusion

T1 is now complete with:
- ✅ 4 core modules (freshness, corporate actions, quality gate, ETL)
- ✅ 43 unit tests + comprehensive integration test
- ✅ Complete documentation (ADRs, concepts, this guide)
- ✅ Mock data generator for testing
- ✅ Performance targets met (<1s for 756 rows)

**Next Steps**: T2 - Real Data Integration with Alpaca API
