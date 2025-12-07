# Fundamental Data Concepts

This document explains fundamental financial data as used in the trading platform, with focus on Compustat data and point-in-time (PIT) correctness for backtesting.

## What is Fundamental Data?

Fundamental data refers to financial metrics from company financial statements:
- **Income Statement**: Revenue (sale), Net Income (ni), Operating Expenses
- **Balance Sheet**: Total Assets (at), Total Liabilities (lt), Common Equity (ceq)
- **Cash Flow Statement**: Operating Cash Flow, Capital Expenditures

This data is reported quarterly (10-Q filings) and annually (10-K filings) by public companies.

## Compustat Overview

Compustat is the gold standard database for US company fundamentals, maintained by S&P Global. Our platform syncs Compustat data from WRDS (Wharton Research Data Services).

### Key Identifier: GVKEY

**GVKEY** (Global Company Key) is Compustat's permanent company identifier, analogous to CRSP's PERMNO.

Important properties:
- Stable across ticker changes (unlike ticker symbols)
- String format (e.g., "001690" for Apple)
- Unique per company entity
- Survives mergers/acquisitions for the surviving entity

**Never use ticker symbols as primary identifiers** - they change over time (e.g., Facebook → Meta).

### Annual vs Quarterly Data

| Aspect | Annual (funda) | Quarterly (fundq) |
|--------|----------------|-------------------|
| Filing | Form 10-K | Form 10-Q |
| Frequency | 1x per year | 4x per year |
| Filing Deadline | ~90 days after FY end | ~45 days after Q end |
| Columns | at, lt, sale, ni, ceq | atq, ltq, saleq, niq |
| Use Case | Long-term analysis | Higher-frequency signals |

### Common Columns

**Annual Data (compustat_annual)**:
```
datadate  - Fiscal period end date
gvkey     - Global Company Key
tic       - Ticker symbol
conm      - Company name
at        - Total Assets
lt        - Total Liabilities
sale      - Net Sales/Revenue
ni        - Net Income
ceq       - Common Equity
```

**Quarterly Data (compustat_quarterly)**:
```
datadate  - Fiscal period end date
gvkey     - Global Company Key
tic       - Ticker symbol
conm      - Company name
atq       - Total Assets (Quarterly)
ltq       - Total Liabilities (Quarterly)
saleq     - Net Sales (Quarterly)
niq       - Net Income (Quarterly)
```

## Point-in-Time (PIT) Correctness

### The Look-Ahead Bias Problem

**Critical**: Using `datadate` as the "as of" date is WRONG.

```
Fiscal Year End: 2023-12-31 (datadate)
10-K Filing Date: 2024-02-15 (when data becomes public)
```

If your backtest uses data from `datadate=2023-12-31` "as of" 2023-12-31, you're implicitly assuming the data was available 46 days before it actually was. This is **look-ahead bias** and will make your backtest results unrealistically good.

### Filing Lag Solution

We use conservative filing lag estimates:

| Filing Type | Default Lag | SEC Deadline |
|-------------|-------------|--------------|
| 10-K (Annual) | 90 days | 60-90 days depending on filer size |
| 10-Q (Quarterly) | 45 days | 40-45 days depending on filer size |

**PIT Rule**: A record with `datadate` is AVAILABLE when:
```
as_of_date >= datadate + filing_lag_days
```

### Code Example

```python
from datetime import date
from libs.data_providers import CompustatLocalProvider

# Create provider
provider = CompustatLocalProvider(
    storage_path=Path("data/wrds"),
    manifest_manager=manifest_mgr,
)

# CORRECT: Get fundamentals available as of April 1, 2024
df = provider.get_annual_fundamentals(
    start_date=date(2023, 1, 1),
    end_date=date(2023, 12, 31),
    as_of_date=date(2024, 4, 1),  # 90+ days after 2023-12-31
    # Records from 2023-12-31 will be included
)

# WARNING: No PIT filtering - may cause look-ahead bias
df_biased = provider.get_annual_fundamentals(
    start_date=date(2023, 1, 1),
    end_date=date(2023, 12, 31),
    # No as_of_date - returns all data regardless of availability
)
```

### Custom Filing Lags

If you know specific filing dates, you can override the default lag:

```python
# Large-cap filers often file faster
df = provider.get_annual_fundamentals(
    start_date=date(2023, 1, 1),
    end_date=date(2023, 12, 31),
    as_of_date=date(2024, 3, 1),
    filing_lag_days=60,  # More aggressive for known fast filers
)
```

## Security Universe Construction

### The Problem

Unlike CRSP (which has explicit IPO/delist dates), Compustat doesn't encode listing windows directly. A company's "existence" in your universe must be derived from filing history.

### Lag-Adjusted Universe Dates

```python
# Raw dates from data
first_datadate = MIN(datadate)  # First filing
last_datadate = MAX(datadate)   # Most recent filing

# Lag-adjusted AVAILABILITY dates
first_available = first_datadate + filing_lag  # When first record became public
last_available = last_datadate + filing_lag    # When last record became public
```

### Universe Query Example

```python
# Get universe of companies with available data as of date
universe = provider.get_security_universe(
    as_of_date=date(2024, 1, 15),
    include_inactive=False,  # Only companies with recent filings
    dataset="quarterly",     # Use quarterly for higher resolution
)

# Result includes:
# - gvkey: Company identifier
# - tic: Point-in-time ticker (as of as_of_date)
# - conm: Company name
# - first_available: When company first appeared
# - last_available: When last filing became public
```

## GVKEY-Ticker Mapping

### Point-in-Time Resolution

Tickers change. Use mapping methods with explicit dates:

```python
# Get ticker for GVKEY as of specific date
ticker = provider.gvkey_to_ticker("001690", date(2023, 6, 1))

# Reverse lookup
gvkey = provider.ticker_to_gvkey("AAPL", date(2023, 6, 1))
```

### Resolution Limitations

Ticker mappings from fundamentals data have limited resolution:
- Annual data: Updated once per year
- Quarterly data: Updated 4x per year

Ticker changes between filings won't be reflected until the next filing. For higher-resolution mapping, consider using Compustat's `comp.names` table (future enhancement).

## Common Pitfalls

### 1. Using Ticker as Identifier

**Wrong**:
```python
# Tickers change! This breaks when FB became META
df = df.filter(pl.col("tic") == "FB")
```

**Correct**:
```python
# GVKEY is stable
df = df.filter(pl.col("gvkey") == "011703")
```

### 2. Ignoring Filing Lag

**Wrong**:
```python
# Assumes data available immediately
df = provider.get_annual_fundamentals(
    start_date=date(2023, 1, 1),
    end_date=date(2023, 12, 31),
    # No as_of_date - look-ahead bias!
)
```

**Correct**:
```python
df = provider.get_annual_fundamentals(
    start_date=date(2023, 1, 1),
    end_date=date(2023, 12, 31),
    as_of_date=date(2024, 4, 1),  # Data available after 90-day lag
)
```

### 3. Missing Compustat Filters

When querying WRDS directly, always include standard filters to avoid duplicate records:

```sql
-- Required filters for Compustat queries
WHERE indfmt = 'INDL'    -- Industrial format (vs financial services)
  AND datafmt = 'STD'    -- Standardized format (vs restated)
  AND popsrc = 'D'       -- Domestic population
  AND consol = 'C'       -- Consolidated statements
```

The SyncManager handles this automatically.

### 4. Survivorship Bias in Universe

**Wrong**:
```python
# Only looking at "active" companies ignores failures
universe = provider.get_security_universe(
    as_of_date=date(2020, 1, 1),
    include_inactive=False,
)
```

**Correct for backtesting**:
```python
# Include companies that existed at the time
universe = provider.get_security_universe(
    as_of_date=date(2020, 1, 1),
    include_inactive=True,  # Include subsequently delisted companies
)
```

## Manifest-Aware Consistency

All queries use manifest-aware snapshot consistency:

1. **Pin manifest version** at query start
2. **Execute query**
3. **Verify manifest unchanged** (raises `ManifestVersionChangedError` if sync occurred)

This ensures you get consistent data even if a sync runs concurrently.

```python
try:
    df = provider.get_annual_fundamentals(...)
except ManifestVersionChangedError:
    # Sync occurred during query - retry
    df = provider.get_annual_fundamentals(...)
```

## Data Storage Layout

```
data/wrds/
├── compustat_annual/
│   ├── 2020.parquet
│   ├── 2021.parquet
│   ├── 2022.parquet
│   └── 2023.parquet
├── compustat_quarterly/
│   ├── 2020.parquet
│   ├── 2021.parquet
│   ├── 2022.parquet
│   └── 2023.parquet
└── ...

data/manifests/
├── compustat_annual.json    # Separate manifest
└── compustat_quarterly.json # Separate manifest
```

Each dataset has its own manifest for independent consistency.

## Related Documentation

- [CRSP Data](./crsp-data.md) - Daily price data concepts
- [Parquet Format](./parquet-format.md) - How data is stored locally
