# Fama-French Factor Data

This document explains Fama-French factor data as used in the trading platform, including factor model construction, data sources, and proper usage.

## What are Fama-French Factors?

Fama-French factors are risk factors developed by Eugene Fama and Kenneth French to explain stock returns beyond the market risk captured by CAPM. The factors represent systematic sources of risk that explain cross-sectional differences in expected returns.

## Factor Models

### 3-Factor Model (Fama-French 1993)

The original model adds two factors to the market risk premium:

| Factor | Description | Long Portfolio | Short Portfolio |
|--------|-------------|----------------|-----------------|
| **Mkt-RF** | Market Risk Premium | Market portfolio | Risk-free rate |
| **SMB** | Small Minus Big | Small-cap stocks | Large-cap stocks |
| **HML** | High Minus Low | High book-to-market | Low book-to-market |
| **RF** | Risk-Free Rate | Treasury bills | N/A |

**Interpretation:**
- SMB captures size premium (small stocks outperform large)
- HML captures value premium (value stocks outperform growth)

### 5-Factor Model (Fama-French 2015)

Extends the 3-factor model with profitability and investment factors:

| Factor | Description | Long Portfolio | Short Portfolio |
|--------|-------------|----------------|-----------------|
| **RMW** | Robust Minus Weak | High profitability | Low profitability |
| **CMA** | Conservative Minus Aggressive | Low investment | High investment |

**Interpretation:**
- RMW captures quality/profitability premium
- CMA captures investment premium (conservative firms outperform)

### 6-Factor Model (with Momentum)

Adds Carhart's momentum factor to the 5-factor model:

| Factor | Description | Long Portfolio | Short Portfolio |
|--------|-------------|----------------|-----------------|
| **UMD** | Up Minus Down | Past winners (12-2 month) | Past losers (12-2 month) |

**Note:** The 6-factor model is sometimes called FF5+Mom or Carhart-extended FF5.

## Data Source

All Fama-French data comes from Kenneth French's Data Library:
- URL: https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/data_library.html
- Updated monthly
- Contains data from 1926 to present

The platform downloads data using `pandas-datareader` and stores it locally in Parquet format.

## Industry Portfolios

Kenneth French also provides industry portfolio returns based on SIC codes:

| Classification | Industries | Example Sectors |
|---------------|------------|-----------------|
| **10-Industry** | 10 broad sectors | Consumer, Manufacturing, Energy, etc. |
| **30-Industry** | 30 sectors | More granular breakdown |
| **49-Industry** | 49 sectors | Fine-grained industry classification |

These portfolios are useful for:
- Industry-level risk analysis
- Sector rotation strategies
- Industry momentum signals

## Return Format

**Important:** All returns in our platform are stored as **decimal** values, not percent.

| Ken French Format | Our Format | Meaning |
|-------------------|------------|---------|
| 1.05 | 0.0105 | 1.05% daily return |
| -0.52 | -0.0052 | -0.52% daily return |

The `FamaFrenchLocalProvider` automatically converts from percent to decimal during sync.

## Usage Examples

### Basic Factor Query

```python
from datetime import date
from libs.data_providers import FamaFrenchLocalProvider
from pathlib import Path

# Initialize provider
provider = FamaFrenchLocalProvider(
    storage_path=Path("data/fama_french"),
)

# Get 3-factor daily data
ff3 = provider.get_factors(
    start_date=date(2024, 1, 1),
    end_date=date(2024, 6, 30),
    model="ff3",
    frequency="daily",
)

# Columns: date, mkt_rf, smb, hml, rf
print(ff3.head())
```

### 5-Factor and 6-Factor Models

```python
# Get 5-factor data (adds RMW, CMA)
ff5 = provider.get_factors(
    start_date=date(2024, 1, 1),
    end_date=date(2024, 6, 30),
    model="ff5",
    frequency="daily",
)

# Get 6-factor data (adds UMD momentum)
ff6 = provider.get_factors(
    start_date=date(2024, 1, 1),
    end_date=date(2024, 6, 30),
    model="ff6",
    frequency="daily",
)
```

### Industry Portfolio Returns

```python
# Get 49-industry portfolio returns
industries = provider.get_industry_returns(
    start_date=date(2024, 1, 1),
    end_date=date(2024, 6, 30),
    num_industries=49,
    frequency="daily",
)
```

### Monthly vs Daily Frequency

```python
# Monthly data (fewer observations, smoother)
ff3_monthly = provider.get_factors(
    start_date=date(2020, 1, 1),
    end_date=date(2024, 12, 31),
    model="ff3",
    frequency="monthly",
)
```

## Syncing Data

Data must be synced before use:

```python
# Sync all datasets
manifest = provider.sync_data()
print(f"Synced {manifest['total_row_count']} rows")

# Sync specific datasets
manifest = provider.sync_data(
    datasets=["factors_3_daily", "factors_5_daily"],
    force=True,  # Re-download even if exists
)
```

Or use the CLI script:

```bash
# Sync all Fama-French data
python scripts/fama_french_sync.py --sync

# Check sync status
python scripts/fama_french_sync.py --status

# Verify data integrity
python scripts/fama_french_sync.py --verify
```

## Storage Layout

```
data/fama_french/
├── factors/
│   ├── factors_3_daily.parquet
│   ├── factors_3_monthly.parquet
│   ├── factors_5_daily.parquet
│   ├── factors_5_monthly.parquet
│   ├── factors_6_daily.parquet      # Materialized 5-factor + momentum
│   ├── factors_6_monthly.parquet
│   ├── momentum_daily.parquet
│   └── momentum_monthly.parquet
├── industries/
│   ├── ind10_daily.parquet
│   ├── ind10_monthly.parquet
│   ├── ind30_daily.parquet
│   ├── ind30_monthly.parquet
│   ├── ind49_daily.parquet
│   └── ind49_monthly.parquet
├── quarantine/                       # Failed/corrupted files
└── fama_french_manifest.json         # Per-file checksums
```

## Common Use Cases

### Factor Regression (Alpha Testing)

```python
import polars as pl
from sklearn.linear_model import LinearRegression

# Get factor data and strategy returns
factors = provider.get_factors(
    start_date=date(2023, 1, 1),
    end_date=date(2023, 12, 31),
    model="ff5",
    frequency="daily",
)

# Merge with strategy returns
merged = strategy_returns.join(factors, on="date")

# Run regression: R_strategy - RF = alpha + beta * factors + epsilon
X = merged.select(["mkt_rf", "smb", "hml", "rmw", "cma"]).to_numpy()
y = (merged["strategy_return"] - merged["rf"]).to_numpy()

model = LinearRegression()
model.fit(X, y)

alpha = model.intercept_  # Alpha (excess return)
betas = dict(zip(["mkt_rf", "smb", "hml", "rmw", "cma"], model.coef_))
```

### Risk Decomposition

```python
# Decompose portfolio risk into factor exposures
factor_exposures = {
    "mkt_rf": 1.05,   # Market beta
    "smb": 0.15,      # Size exposure (slightly small-cap tilted)
    "hml": 0.30,      # Value exposure
    "rmw": 0.10,      # Quality exposure
    "cma": -0.05,     # Investment exposure (slight growth tilt)
}

# Calculate factor contribution to variance
factor_variances = factors.select([
    pl.col(c).var().alias(f"{c}_var") for c in factor_exposures.keys()
])
```

## Data Quality Notes

1. **No Point-in-Time Concerns**: Unlike fundamental data (Compustat), factor data has no filing lag. The factors are published after market close with no look-ahead bias.

2. **Monthly Updates**: Ken French typically updates data monthly. Sync weekly or monthly is sufficient.

3. **Historical Revisions**: Factor returns are occasionally revised. The platform stores the current version; consider versioning for reproducibility.

4. **Missing Data**: Some historical periods may have gaps. Always check for nulls before analysis.

## Related Documentation

- [CRSP Data](./crsp-data.md) - Daily price data concepts
- [Fundamental Data (Compustat)](./fundamental-data.md) - Financial statement data
