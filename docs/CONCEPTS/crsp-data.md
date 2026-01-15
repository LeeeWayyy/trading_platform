# CRSP Data

## Plain English Explanation

CRSP (Center for Research in Security Prices) is the gold standard for historical US stock data in academic research. It provides daily stock prices, returns, and metadata going back to 1925.

**Simple analogy:**
- **Yahoo Finance:** Free newspaper stock tables - convenient but may have errors
- **CRSP:** Official library archives - authoritative, academically verified, consistent

### Key Identifiers

**PERMNO (Permanent Number):**
- Unique identifier that **stays the same** throughout a company's life
- Never reused, even after delisting
- Think of it like a Social Security Number for stocks

**Ticker Symbol:**
- The trading symbol you see (AAPL, MSFT)
- **Changes over time** (Google was GOOG, added GOOGL)
- **Gets reused** after delisting (old YAHOO ticker is now a different company)

**Visual Example:**
```
Date         PERMNO   Ticker    Price
2020-01-02   14593    GOOG      $1368.68
2020-01-03   14593    GOOG      $1361.52
...
2022-07-15   14593    GOOGL     $107.90   ← Same company, ticker changed (stock split)
2022-07-18   14593    GOOGL     $110.42
```

## Why It Matters for Trading

### 1. Survivorship Bias Prevention

**The problem:**
```python
# ❌ WRONG - Only looking at stocks that exist today
current_sp500 = get_current_sp500_constituents()
backtest(stocks=current_sp500, start='2010-01-01')
# Result: Backtest looks amazing! (Because failed companies aren't included)
```

**CRSP solution:**
```python
# ✅ CORRECT - Include delisted stocks
from libs.data.data_providers import CRSPLocalProvider

provider = CRSPLocalProvider(...)

# Get universe as it existed on 2010-01-01 (includes companies that later delisted)
universe = provider.get_universe(
    as_of_date=date(2010, 1, 1),
    include_delisted=True  # Include Lehman, Bear Stearns, etc.
)
```

**Real impact:**
- Without CRSP: Backtest shows +15% annual return
- With CRSP (survivorship-free): Actual return +8%

### 2. Point-in-Time Data Access

**The problem:** Using future information in historical analysis.

```python
# ❌ WRONG - Using 2024 ticker mapping for 2010 data
ticker_2024 = "META"  # Facebook's current ticker
# META didn't exist until 2022! Facebook was FB
prices_2010 = get_prices(ticker_2024, start='2010-01-01')  # Fails or wrong data
```

**CRSP solution:**
```python
# ✅ CORRECT - Use PERMNO (stable identifier)
permno = 13407  # Facebook's permanent CRSP ID
prices = provider.get_daily_prices(
    start_date=date(2010, 1, 1),
    end_date=date(2024, 1, 1),
    permnos=[permno]  # Works regardless of ticker changes (FB → META)
)
```

### 3. Handling Negative Prices

**CRSP convention:** Negative prices indicate **no closing price available**.

```python
# CRSP raw data
date        permno  ticker    prc
2024-01-02  14593   GOOGL     150.23    # Normal closing price
2024-01-03  14593   GOOGL    -151.00    # NEGATIVE = bid/ask average (no close)
```

**Why this happens:**
- Stock didn't trade at close (illiquid)
- Trading halt
- Holiday/half-day

**Our solution:**
```python
# Get raw prices (preserves CRSP convention)
df = provider.get_daily_prices(..., adjust_prices=False)
# prc column has negative values

# Get absolute prices (for most use cases)
df = provider.get_daily_prices(..., adjust_prices=True)
# prc column is always positive (uses abs() on values)
```

## Common Pitfalls

### 1. Using Tickers Instead of PERMNOs

**Problem:** Tickers change and get reused.

```python
# ❌ WRONG - Ticker lookup without date context
permno = provider.ticker_to_permno("YAHOO")  # Which YAHOO? Old or new?
```

```python
# ✅ CORRECT - Always provide as_of_date
permno = provider.ticker_to_permno("YAHOO", as_of_date=date(2016, 1, 1))
# Returns the original Yahoo (acquired by Verizon)

permno = provider.ticker_to_permno("YAHOO", as_of_date=date(2020, 1, 1))
# Might raise DataNotFoundError or return different company
```

### 2. Ambiguous Ticker Errors

**Problem:** Same ticker, multiple companies on same date.

```python
# Example: During mergers/spinoffs
try:
    permno = provider.ticker_to_permno("DELL", as_of_date=date(2016, 9, 7))
except AmbiguousTickerError as e:
    print(f"Ambiguous: {e.ticker} maps to {e.permnos}")
    # User must choose which PERMNO they mean
```

**Solution:** Use PERMNO directly when you know the company.

### 3. Not Considering IPO Dates

**Problem:** Querying data before a stock existed.

```python
# ❌ WRONG - Tesla IPO was 2010-06-29
provider.get_daily_prices(
    start_date=date(2005, 1, 1),
    permnos=[93436]  # Tesla PERMNO
)
# Returns empty DataFrame for 2005-2010
```

```python
# ✅ CORRECT - Use point-in-time filtering
provider.get_daily_prices(
    start_date=date(2005, 1, 1),
    permnos=[93436],
    as_of_date=date(2005, 1, 1)  # Exclude stocks that didn't exist
)
# Tesla excluded from results (wasn't public yet)
```

### 4. Ignoring Manifest Consistency

**Problem:** Data changes during long-running queries.

```python
# ❌ RISKY - Long analysis without consistency check
provider.get_daily_prices(...)  # Takes 30 seconds
# Meanwhile, sync_manager runs and updates data
# Your analysis might have inconsistent data!
```

```python
# ✅ SAFE - Manifest version pinning (automatic)
try:
    df = provider.get_daily_prices(...)  # Version pinned at start
except ManifestVersionChangedError:
    # Data was updated during query - retry
    df = provider.get_daily_prices(...)
```

## Examples

### Example 1: Basic Price Query

```python
from datetime import date
from libs.data.data_providers import CRSPLocalProvider
from libs.data.data_quality.manifest import ManifestManager

# Initialize provider
manifest_mgr = ManifestManager(Path("data/manifests"))
provider = CRSPLocalProvider(
    storage_path=Path("data/wrds/crsp/daily"),
    manifest_manager=manifest_mgr,
)

# Get daily prices for specific securities
df = provider.get_daily_prices(
    start_date=date(2023, 1, 1),
    end_date=date(2023, 12, 31),
    symbols=["AAPL", "MSFT", "GOOGL"],
    columns=["date", "permno", "ticker", "prc", "ret"],
    adjust_prices=True,  # Get absolute values
)

print(df.head())
```

**Output:**
```
shape: (5, 5)
┌────────────┬────────┬────────┬────────┬──────────┐
│ date       ┆ permno ┆ ticker ┆ prc    ┆ ret      │
│ ---        ┆ ---    ┆ ---    ┆ ---    ┆ ---      │
│ date       ┆ i64    ┆ str    ┆ f64    ┆ f64      │
╞════════════╪════════╪════════╪════════╪══════════╡
│ 2023-01-03 ┆ 14593  ┆ GOOGL  ┆ 89.70  ┆ -0.0104  │
│ 2023-01-03 ┆ 10107  ┆ MSFT   ┆ 239.58 ┆ -0.0041  │
│ 2023-01-03 ┆ 14593  ┆ AAPL   ┆ 125.07 ┆ -0.0386  │
│ 2023-01-04 ┆ 14593  ┆ GOOGL  ┆ 88.08  ┆ -0.0181  │
│ 2023-01-04 ┆ 10107  ┆ MSFT   ┆ 229.10 ┆ -0.0437  │
└────────────┴────────┴────────┴────────┴──────────┘
```

### Example 2: Point-in-Time Universe

```python
# Get all stocks that were trading on a specific date
universe_2020 = provider.get_universe(
    as_of_date=date(2020, 1, 2),
    include_delisted=True,  # Include stocks that later delisted
)

print(f"Universe size: {len(universe_2020)}")
print(universe_2020.head())
```

**Output:**
```
Universe size: 8,542

shape: (5, 5)
┌────────┬────────┬──────────┬────────────┬────────────┐
│ permno ┆ ticker ┆ cusip    ┆ first_date ┆ last_date  │
│ ---    ┆ ---    ┆ ---      ┆ ---        ┆ ---        │
│ i64    ┆ str    ┆ str      ┆ date       ┆ date       │
╞════════╪════════╪══════════╪════════════╪════════════╡
│ 10001  ┆ AMAG   ┆ 00166010 ┆ 2013-04-10 ┆ 2020-08-06 │  ← Delisted in 2020
│ 10002  ┆ AAPL   ┆ 03783310 ┆ 1980-12-12 ┆ 2024-01-02 │  ← Still trading
│ 10003  ┆ GE     ┆ 36960410 ┆ 1962-01-02 ┆ 2024-01-02 │
│ 10004  ┆ IBM    ┆ 45920010 ┆ 1962-01-02 ┆ 2024-01-02 │
│ 10005  ┆ LEHM   ┆ 52490010 ┆ 1984-05-31 ┆ 2008-09-17 │  ← Lehman Brothers
└────────┴────────┴──────────┴────────────┴────────────┘
```

### Example 3: Ticker-PERMNO Mapping

```python
# Map ticker to PERMNO at specific date
try:
    permno = provider.ticker_to_permno("FB", as_of_date=date(2020, 1, 1))
    print(f"FB on 2020-01-01: PERMNO {permno}")  # Facebook

    permno = provider.ticker_to_permno("META", as_of_date=date(2023, 1, 1))
    print(f"META on 2023-01-01: PERMNO {permno}")  # Same company, new ticker

except DataNotFoundError as e:
    print(f"Not found: {e}")
```

### Example 4: Security Timeline

```python
# Track ticker changes for a security
timeline = provider.get_security_timeline(permno=14593)  # Google

print("Google's ticker history:")
ticker_changes = timeline.group_by("ticker").agg([
    pl.col("date").min().alias("first_used"),
    pl.col("date").max().alias("last_used"),
])
print(ticker_changes.sort("first_used"))
```

**Output:**
```
Google's ticker history:
shape: (2, 3)
┌────────┬────────────┬────────────┐
│ ticker ┆ first_used ┆ last_used  │
│ ---    ┆ ---        ┆ ---        │
│ str    ┆ date       ┆ date       │
╞════════╪════════════╪════════════╡
│ GOOG   ┆ 2004-08-19 ┆ 2014-04-02 │
│ GOOGL  ┆ 2014-04-03 ┆ 2024-01-02 │
└────────┴────────────┴────────────┘
```

## Key Concepts

### CRSP Daily File Structure

Our local CRSP data is stored as yearly Parquet partitions:

```
data/wrds/crsp/daily/
├── 2020.parquet
├── 2021.parquet
├── 2022.parquet
├── 2023.parquet
└── 2024.parquet
```

**Schema:**

| Column | Type | Description |
|--------|------|-------------|
| date | Date | Trading day |
| permno | Int64 | CRSP permanent identifier |
| cusip | String | CUSIP identifier |
| ticker | String | Stock ticker (can change) |
| ret | Float64 | Holding period return |
| prc | Float64 | Closing price (negative = bid/ask average) |
| vol | Float64 | Trading volume |
| shrout | Float64 | Shares outstanding |

### Manifest-Aware Queries

Every query pins the manifest version to ensure consistency:

```
Query Start ──► Pin manifest v1.2.3 ──► Execute Query ──► Verify still v1.2.3 ──► Return
                                                              │
                                                              ▼
                                                     If changed: ManifestVersionChangedError
```

### Partition Pruning

Queries only read necessary year files:

```python
# Query for 2023 Q1 only reads 2023.parquet
provider.get_daily_prices(
    start_date=date(2023, 1, 1),
    end_date=date(2023, 3, 31),
)
# Reads: data/wrds/crsp/daily/2023.parquet (only this file)
# Skips: 2020.parquet, 2021.parquet, 2022.parquet, 2024.parquet
```

### Security Validation

The provider validates all paths to prevent directory traversal:

```python
# Paths must be within data_root
provider = CRSPLocalProvider(
    storage_path=Path("data/wrds/crsp/daily"),
    data_root=Path("data"),  # All queries confined here
)

# Paths like "../../../etc/passwd" in manifest are rejected
```

## Best Practices

### 1. Use PERMNOs for Backtesting

```python
# ✅ CORRECT - Stable identifier
backtest_securities = [14593, 10107, 93436]  # GOOGL, MSFT, TSLA PERMNOs
df = provider.get_daily_prices(permnos=backtest_securities, ...)

# ❌ AVOID - Tickers can change
backtest_tickers = ["GOOGL", "MSFT", "TSLA"]
df = provider.get_daily_prices(symbols=backtest_tickers, ...)
```

### 2. Always Use adjust_prices for Analysis

```python
# For returns calculation, use adjusted prices
df = provider.get_daily_prices(..., adjust_prices=True)
returns = df.select([
    pl.col("date"),
    pl.col("permno"),
    pl.col("prc").pct_change().alias("calc_ret"),
])
```

### 3. Cache Security Metadata

```python
# Metadata is cached automatically, but invalidate after sync
provider.invalidate_cache()  # Call after sync_manager.full_sync()
```

### 4. Handle Manifest Changes Gracefully

```python
from libs.data.data_providers import ManifestVersionChangedError

max_retries = 3
for attempt in range(max_retries):
    try:
        df = provider.get_daily_prices(...)
        break
    except ManifestVersionChangedError:
        if attempt == max_retries - 1:
            raise
        time.sleep(0.1)  # Brief pause, then retry
```

### 5. Use Context Manager

```python
# Ensures connection cleanup
with CRSPLocalProvider(...) as provider:
    df = provider.get_daily_prices(...)
# Connection automatically closed
```

## Performance Characteristics

### Query Performance

| Query Type | Data Size | Time |
|------------|-----------|------|
| Single stock, 1 year | ~250 rows | <100ms |
| 500 stocks, 1 year | ~125K rows | ~500ms |
| All stocks, 1 year | ~2M rows | ~2s |
| All stocks, 5 years | ~10M rows | ~8s |

### Memory Usage

| Operation | Memory |
|-----------|--------|
| Provider initialization | ~10 MB |
| Security metadata cache | ~50 MB (for ~10K securities) |
| Typical query result | 10-500 MB |
| DuckDB memory limit | 2 GB (configurable) |

## Error Reference

| Error | Cause | Solution |
|-------|-------|----------|
| `DataNotFoundError` | No manifest or no matching data | Run sync first, check date range |
| `AmbiguousTickerError` | Ticker maps to multiple PERMNOs | Use PERMNO directly or disambiguate by date |
| `ManifestVersionChangedError` | Data updated during query | Retry the query |
| `ValueError: Invalid columns` | Requested unknown columns | Check VALID_COLUMNS |
| `ValueError: storage_path outside data_root` | Security violation | Use path within data/ |

## Further Reading

- [CRSP Official Documentation](https://www.crsp.org/) - Official CRSP website
- [Survivorship Bias in Finance](https://en.wikipedia.org/wiki/Survivorship_bias)

## Related Concepts

- [parquet-format.md](./parquet-format.md) - Parquet file format basics
- [duckdb-basics.md](./duckdb-basics.md) - DuckDB query patterns
- [qlib-data-providers.md](./qlib-data-providers.md) - Qlib data integration

---

**Last Updated:** 2025-12-04
**Relates To:** P4T1.3 - CRSP Local Provider
