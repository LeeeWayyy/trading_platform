# Qlib Data Providers

## Plain English Explanation

A Qlib data provider is a bridge that tells Qlib where to get market data from. Think of it like a translator that converts your data format into the format Qlib expects.

Qlib normally expects data in its own special format stored in specific directories. But we already have data from our T1 pipeline saved as Parquet files with adjusted prices. Instead of converting everything to Qlib's format, we create a "custom data provider" that reads our Parquet files and gives Qlib the data it needs in real-time.

**Simple analogy:** Imagine Qlib speaks French and your data speaks English. A data provider is the translator that lets them communicate.

## Why It Matters

Without a custom data provider, you would need to:
1. Export T1's adjusted data
2. Convert it to Qlib's format
3. Store it in Qlib's expected directory structure
4. Re-run this conversion every time data updates

This creates:
- **Data duplication** - Same data stored twice
- **Sync issues** - T1 updates but Qlib's copy is stale
- **Extra complexity** - Another pipeline to maintain
- **Slower iteration** - Can't quickly test on latest data

With a custom provider:
- ✅ Single source of truth (T1's Parquet files)
- ✅ Always up-to-date (reads latest files)
- ✅ No conversion pipeline needed
- ✅ Direct integration with our infrastructure

## Common Pitfalls

### 1. Wrong DataFrame Index
**Problem:** Qlib expects `pd.DataFrame` with `(date, symbol)` MultiIndex, but you provide single index.

**Symptoms:**
```python
KeyError: 'symbol not found in index'
```

**Solution:**
```python
# WRONG - Single index
df = df.set_index("date")

# RIGHT - MultiIndex (date, symbol)
df = df.set_index(["date", "symbol"])
```

### 2. Incorrect Column Names
**Problem:** Qlib uses lowercase column names (`open`, `high`, `low`, `close`, `volume`), but your data has mixed case.

**Symptoms:**
```python
KeyError: 'Close' not found
```

**Solution:**
```python
# Standardize column names to lowercase
df.columns = [col.lower() for col in df.columns]
```

### 3. Missing or Misaligned Dates
**Problem:** Different symbols have different trading days (holidays, IPO dates), causing misalignment.

**Symptoms:**
- Features calculated incorrectly
- Look-ahead bias in features
- NaN values propagating unexpectedly

**Solution:**
```python
# Fill missing dates with forward fill (last valid price)
df = df.groupby("symbol").fillna(method="ffill")

# Or use Qlib's built-in handling (it expects gaps)
# Qlib will handle this correctly if dates are proper datetime
```

### 4. Data Not Sorted
**Problem:** Qlib assumes data is sorted by date for time-series operations.

**Symptoms:**
- Momentum features look backwards instead of forwards
- Returns calculated incorrectly
- Features computed out of order

**Solution:**
```python
# Always sort by symbol and date before returning
df = df.sort_values(["symbol", "date"])
```

## Examples

### Example 1: Basic Custom Provider

```python
import polars as pl
import pandas as pd
from pathlib import Path

class T1DataProvider:
    """Load adjusted data from T1 Parquet files."""

    def __init__(self, data_dir: Path = Path("data/adjusted")):
        self.data_dir = data_dir

    def load_data(
        self,
        symbols: list[str],
        start_date: str,
        end_date: str
    ) -> pd.DataFrame:
        """
        Load data in Qlib format.

        Returns:
            DataFrame with (date, symbol) MultiIndex and OHLCV columns
        """
        # Step 1: Load all Parquet files for requested symbols
        dfs = []
        for symbol in symbols:
            parquet_files = list(self.data_dir.rglob(f"{symbol}.parquet"))
            if parquet_files:
                df = pl.read_parquet(parquet_files[0])
                dfs.append(df)

        if not dfs:
            # Return empty DataFrame with correct structure
            return pd.DataFrame(
                columns=["open", "high", "low", "close", "volume"]
            ).set_index([pd.Index([]), pd.Index([])])

        # Step 2: Concatenate all symbols
        combined = pl.concat(dfs)

        # Step 3: Filter date range
        combined = combined.filter(
            (pl.col("date") >= pl.lit(start_date).str.to_date()) &
            (pl.col("date") <= pl.lit(end_date).str.to_date())
        )

        # Step 4: Convert to Pandas (Qlib expects Pandas)
        df = combined.to_pandas()

        # Step 5: Ensure lowercase column names
        df.columns = [col.lower() for col in df.columns]

        # Step 6: Sort by symbol and date
        df = df.sort_values(["symbol", "date"])

        # Step 7: Set MultiIndex (date, symbol) - THIS IS CRITICAL
        df = df.set_index(["date", "symbol"])

        # Step 8: Keep only OHLCV columns
        ohlcv_cols = ["open", "high", "low", "close", "volume"]
        df = df[ohlcv_cols]

        return df
```

**Usage:**
```python
provider = T1DataProvider()
data = provider.load_data(
    symbols=["AAPL", "MSFT"],
    start_date="2024-01-01",
    end_date="2024-12-31"
)

# Check structure
print(data.index.names)  # ['date', 'symbol']
print(data.columns)      # ['open', 'high', 'low', 'close', 'volume']
print(data.head())
```

**Output:**
```
                            open    high     low   close    volume
date       symbol
2024-01-01 AAPL         150.0   152.0   148.0   151.0  1000000.0
           MSFT         350.0   355.0   348.0   352.0   800000.0
2024-01-02 AAPL         151.5   153.0   150.0   152.0  1100000.0
           MSFT         353.0   358.0   351.0   356.0   850000.0
```

### Example 2: Handling Corporate Actions

Our T1 pipeline already adjusts for corporate actions (splits, dividends). The custom provider just passes this adjusted data to Qlib.

**Before adjustment (raw data):**
```
date       symbol  close
2024-08-30 AAPL    500.0  ← Before split
2024-08-31 AAPL    125.0  ← After 4:1 split (looks like 75% crash!)
2024-09-01 AAPL    130.0
```

**After T1 adjustment (what Qlib sees):**
```
date       symbol  close
2024-08-30 AAPL    125.0  ← Adjusted (500 / 4)
2024-08-31 AAPL    125.0  ← Already adjusted
2024-09-01 AAPL    130.0  ← No adjustment needed
```

Qlib can now calculate returns correctly:
- 2024-08-30 → 2024-08-31: 0% return (not -75%)
- 2024-08-31 → 2024-09-01: 4% return ✓

### Example 3: Integration with Qlib's DataHandler

```python
from qlib.data.dataset.handler import DataHandlerLP

class CustomDataHandler(DataHandlerLP):
    """Qlib DataHandler that uses T1 data provider."""

    def __init__(self, symbols, start_date, end_date):
        # Initialize T1 provider
        self.provider = T1DataProvider()

        # Load data
        self.data = self.provider.load_data(
            symbols=symbols,
            start_date=start_date,
            end_date=end_date
        )

        super().__init__(
            instruments=symbols,
            start_time=start_date,
            end_time=end_date
        )

    def fetch(self, col_set="feature"):
        """
        Qlib calls this to get data.

        Args:
            col_set: "feature" or "label"

        Returns:
            DataFrame with requested columns
        """
        if col_set == "feature":
            # Return OHLCV for feature engineering
            return self.data
        elif col_set == "label":
            # Calculate label (next day return)
            returns = self.data["close"].pct_change(1).shift(-1)
            return returns.to_frame("label")
```

**Usage with Qlib:**
```python
import qlib
from qlib.contrib.data.handler import Alpha158

# Initialize Qlib
qlib.init()

# Use custom handler with Alpha158 features
handler = CustomDataHandler(
    symbols=["AAPL", "MSFT", "GOOGL"],
    start_date="2024-01-01",
    end_date="2024-12-31"
)

# Alpha158 will now compute features on T1's adjusted data
features = handler.fetch(col_set="feature")
labels = handler.fetch(col_set="label")
```

## Further Reading

- [Qlib Data Documentation](https://qlib.readthedocs.io/en/latest/component/data.html)
- [Pandas MultiIndex Guide](https://pandas.pydata.org/docs/user_guide/advanced.html)
- [Polars to Pandas Conversion](https://pola-rs.github.io/polars/py-polars/html/reference/api/polars.DataFrame.to_pandas.html)
- See `/docs/IMPLEMENTATION_GUIDES/t1-data-etl.md` for T1 pipeline details
- See `/docs/CONCEPTS/corporate-actions.md` for adjustment explanation
