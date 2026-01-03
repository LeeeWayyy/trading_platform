# yfinance Limitations

**Status:** Development-only data source
**Provider:** `YFinanceProvider`

---

## Overview

yfinance is a free market data provider that downloads historical data from Yahoo Finance. It's useful for development and testing but **NOT suitable for production backtests** due to significant data quality limitations.

---

## Critical Limitations

### 1. No Survivorship Bias Handling

yfinance only provides data for currently trading stocks. It does **NOT** include:
- Delisted stocks
- Bankrupt companies
- Acquired/merged companies

**Impact:** Backtests using yfinance will be overly optimistic because they only see "winners" - stocks that survived to the present day.

### 2. Missing Corporate Actions

yfinance does not properly handle:
- Stock splits (inconsistent adjustment)
- Dividends (reinvestment assumptions vary)
- Spinoffs
- Mergers and acquisitions

**Impact:** Returns calculations may be incorrect, leading to false signals.

### 3. Data Quality Issues

- **Delayed updates:** Data may lag by hours or days
- **Missing data:** Gaps in historical data are common
- **Incorrect prices:** Occasional errors in OHLCV data
- **Rate limiting:** Yahoo aggressively throttles requests

### 4. No Point-in-Time Guarantees

yfinance data can change retroactively as Yahoo corrects errors. There's no guarantee that today's download matches tomorrow's download for the same date range.

---

## When to Use yfinance

✅ **Appropriate uses:**
- Local development and testing
- Learning and experimentation
- Quick prototyping
- Feature development (with mock data validation)

❌ **NOT appropriate for:**
- Production backtests
- Performance reporting
- Trading decisions
- Academic research requiring reproducibility

---

## Production Gating

The `YFinanceProvider` includes automatic production gating:

```python
# These conditions block yfinance in production:
# 1. CRSP data available → yfinance blocked (use CRSP)
# 2. Environment = production + use_yfinance_in_prod = False → blocked
# 3. Environment = production + use_yfinance_in_prod = True → warned + allowed
```

### Environment Matrix

| Environment | CRSP Available | Flag | Result |
|-------------|----------------|------|--------|
| development | Any | Any | ✅ Allowed |
| test | Any | Any | ✅ Allowed |
| staging | Any | Any | ⚠️ Warned + Allowed |
| production | Yes | Any | ❌ Blocked |
| production | No | False | ❌ Blocked |
| production | No | True | ⚠️ Warned + Allowed |

---

## Drift Detection

To catch data quality issues, the provider supports drift detection against a baseline:

```python
# Check if yfinance prices drift >1% from baseline
passed, max_drift = provider.check_drift(symbol="SPY")

if not passed:
    logger.warning(f"Drift detected: {max_drift:.2%}")
```

### Baseline Files

Store verified baseline data in `data/baseline/`:
```
data/baseline/
├── spy_60d.parquet      # Last 60 trading days
├── qqq_60d.parquet
└── baseline_manifest.json
```

---

## Recommended Alternative: CRSP

For production backtests, use CRSP data via `CRSPLocalProvider`:

| Feature | yfinance | CRSP |
|---------|----------|------|
| Survivorship bias handling | ❌ No | ✅ Yes |
| Corporate actions | ❌ Inconsistent | ✅ Complete |
| Point-in-time data | ❌ No | ✅ Yes |
| Data quality | ⚠️ Variable | ✅ Academic-grade |
| Cost | Free | Academic subscription |

---

## Usage Example

```python
from libs.data_providers import YFinanceProvider

# Development use
provider = YFinanceProvider(
    storage_path=Path("data/yfinance"),
    environment="development",  # Allowed
)

# Fetch with caching
df = provider.get_daily_prices(
    symbols=["SPY", "AAPL"],
    start_date=date(2024, 1, 1),
    end_date=date(2024, 12, 31),
)

# Production use (blocked by default)
prod_provider = YFinanceProvider(
    storage_path=Path("data/yfinance"),
    environment="production",
    crsp_available=True,  # CRSP available
)
# → Raises ProductionGateError
```

---

## References

- [P4T1_TASK.md](../ARCHIVE/TASKS_HISTORY/P4T1_DONE.md) - Task specification
- [CRSPLocalProvider](./crsp-data.md) - Production-grade alternative
- [Yahoo Finance Terms](https://policies.yahoo.com/us/en/yahoo/terms/product-atos/apiforydn/index.htm) - Data usage terms

---

**Last Updated:** 2025-12-05
