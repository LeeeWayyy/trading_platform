# Feature Parity

## Plain English Explanation

Feature parity means using **the exact same code** to generate features in both research (backtesting) and production (live trading). No duplication, no reimplementation, no "production version" of your research code.

**The problem it solves:**

You backtest a strategy in a Jupyter notebook:
```python
# research/backtest.ipynb
features = compute_alpha158_features(data)
model = train_model(features, returns)
# Backtest shows 15% annual return, Sharpe 1.8 🎉
```

Then you rewrite it for production:
```python
# production/signal_service.py
features = compute_features_for_production(data)  # Reimplemented!
predictions = model.predict(features)
```

**Result:** Live trading returns 3% annual return, Sharpe 0.4. What went wrong?

**The bug:** Your production feature computation had a subtle difference:
- Research: Used forward-fill then backfill for NaNs
- Production: Used only forward-fill

This created **train-serve skew** - model trained on different features than it sees in production.

**Feature parity solution:**
```python
# strategies/alpha_baseline/features.py (ONE implementation)
def get_alpha158_features(data): ...

# research/backtest.ipynb
from strategies.alpha_baseline.features import get_alpha158_features
features = get_alpha158_features(data)  # Use shared code

# production/signal_service.py
from strategies.alpha_baseline.features import get_alpha158_features
features = get_alpha158_features(data)  # Same shared code
```

Now research and production use **identical feature generation**, guaranteeing parity.

## Why It Matters

### Real-World Impact

**Without feature parity (train-serve skew):**

**Example 1: Moving Average Bug**
```python
# Research (correct)
ma_20 = close.rolling(20).mean()

# Production (bug)
ma_20 = close.rolling(20, min_periods=1).mean()  # Different!
```

Impact:
- First 19 days: Production uses shorter window (1-19 periods vs 20)
- Model trained on 20-day MA, gets 1-19 day MA in production
- Predictions are garbage for the first month of each symbol's data
- **Real loss:** Strategy shows +15% in backtest, -8% in live trading

**Example 2: Price Adjustment Bug**
```python
# Research
returns = (adjusted_close / adjusted_close.shift(1)) - 1

# Production
returns = (close / close.shift(1)) - 1  # Forgot "adjusted"!
```

Impact:
- Stock split happens: AAPL 4:1 split
- Research sees: -75% return (400→100, adjusted correctly)
- Production sees: Real -75% crash, model thinks market collapsed
- Model goes max short, loses money as stock continues normal trading
- **Real loss:** Single bad signal costs thousands

**Example 3: Timezone Bug**
```python
# Research (pandas default)
df['date'] = pd.to_datetime(df['date'])  # Assumes UTC

# Production (explicit timezone)
df['date'] = pd.to_datetime(df['date'], utc=True).tz_convert('America/New_York')
```

Impact:
- Research and production have 4-5 hour difference in daily bars
- Production uses today's data to predict today (look-ahead bias)
- Backtest unrealistically good, production mediocre
- **Real loss:** Overallocation to strategy based on inflated backtest returns

### Industry Statistics

- **83% of ML models** degrade within first month of production (Google Research)
- **Train-serve skew** is the #2 cause (after data quality issues)
- Average impact: **40-60% reduction** in model performance

**Why so common?**
1. Different engineers for research vs production
2. Different languages (Python research, Java production)
3. Time pressure ("just ship something that works")
4. Lack of integration tests

## Common Pitfalls

### Pitfall 1: "Close Enough" Implementations

**Problem:** Reimplementing features with "similar" logic.

**Example:**
```python
# Research (numpy)
def compute_rsi(prices, period=14):
    deltas = np.diff(prices)
    seed = deltas[:period+1]
    up = seed[seed >= 0].sum() / period
    down = -seed[seed < 0].sum() / period
    rs = up / down
    rsi = 100 - (100 / (1 + rs))
    return rsi

# Production (pandas) - "reimplemented from scratch"
def compute_rsi_prod(prices, period=14):
    delta = prices.diff()
    gain = (delta.where(delta > 0, 0)).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return rsi
```

**The bug:** Research uses Wilder's smoothing (exponential), production uses simple moving average. These produce different values!

**Fix:** Use ONE implementation:
```python
# shared/indicators.py
def compute_rsi(prices, period=14):
    """RSI using Wilder's smoothing (industry standard)."""
    # ... implementation
    return rsi

# Both research and production
from shared.indicators import compute_rsi
```

### Pitfall 2: Copy-Paste Drift

**Problem:** Copying code into multiple places, then modifying one copy.

**Example:**
```python
# Initial: Copy research code to production
# research/features.py
def normalize_features(df):
    return (df - df.mean()) / df.std()

# production/features.py (copy-pasted)
def normalize_features(df):
    return (df - df.mean()) / df.std()

# Later: Bug fix in research, forgot to update production
# research/features.py
def normalize_features(df):
    # Fix: Ignore NaN when computing stats
    return (df - df.mean(skipna=True)) / df.std(skipna=True)

# production/features.py (NOT updated - BUG!)
def normalize_features(df):
    return (df - df.mean()) / df.std()  # Still has NaN bug
```

**Fix:** Don't copy. Import from single source:
```python
# libs/features/normalization.py (single source of truth)
def normalize_features(df):
    return (df - df.mean(skipna=True)) / df.std(skipna=True)

# research/backtest.ipynb
from libs.features.normalization import normalize_features

# production/signal_service.py
from libs.features.normalization import normalize_features
```

Now bug fixes automatically propagate to both.

### Pitfall 3: Hidden State Differences

**Problem:** Code looks identical but uses different global state.

**Example:**
```python
# Config file differences
# research_config.py
DATA_START_DATE = "2020-01-01"  # 4 years of history

# production_config.py
DATA_START_DATE = "2023-01-01"  # Only 1 year (save money)

# Shared feature code
def get_features(symbols, end_date):
    start_date = config.DATA_START_DATE  # Different in research vs prod!
    data = fetch_data(symbols, start_date, end_date)
    return compute_features(data)
```

**The bug:** Research model trained on 4 years, production uses 1 year. Features like "52-week high" behave differently.

**Fix:** Make dependencies explicit:
```python
def get_features(symbols, end_date, history_days=1000):
    """Get features with explicit history requirement."""
    start_date = end_date - timedelta(days=history_days)
    data = fetch_data(symbols, start_date, end_date)
    return compute_features(data)

# Both research and production use same parameters
features = get_features(symbols, date, history_days=1000)
```

### Pitfall 4: Library Version Differences

**Problem:** Research and production use different library versions.

**Example:**
```python
# Research environment
pandas==1.5.3  # Old version

# Production environment
pandas==2.0.0  # New version

# Shared code
df.fillna(method='ffill')  # Deprecated in 2.0.0!
```

**Result:** Production raises warnings or errors that research doesn't see.

**Fix:** Pin exact versions in both environments:
```
# requirements.txt (shared by research and production)
pandas==2.0.3
numpy==1.24.3
polars==0.20.31
```

Use tools like `poetry` or `pip-tools` to ensure identical environments.

### Pitfall 5: Date Handling Inconsistencies

**Problem:** Different date filtering or timezone handling.

**Example:**
```python
# Research: Inclusive end date
data = df[(df['date'] >= start) & (df['date'] <= end)]

# Production: Exclusive end date (forgot the "=")
data = df[(df['date'] >= start) & (df['date'] < end)]
```

**Result:** Production missing the last day's data, features computed on one less day.

**Fix:** Use a single data loader:
```python
# shared/data/loader.py
def load_data(symbols, start_date, end_date, inclusive_end=True):
    """Load OHLCV data for symbols.

    Args:
        inclusive_end: If True, include end_date. If False, exclude it.
                       Default True to match backtesting convention.
    """
    if inclusive_end:
        mask = (df['date'] >= start_date) & (df['date'] <= end_date)
    else:
        mask = (df['date'] >= start_date) & (df['date'] < end_date)
    return df[mask]
```

## Examples

### Example 1: Alpha158 Feature Parity

**Background:** Alpha158 is a feature set from Qlib with 158 technical indicators.

**Without parity (BAD):**
```python
# research/notebooks/alpha_baseline_backtest.ipynb
def compute_alpha158(df):
    """Compute 158 features. 500 lines of code."""
    features = pd.DataFrame()
    # KBAR features
    features['KBAR_OPEN'] = df['open'] / df['close'] - 1
    features['KBAR_HIGH'] = df['high'] / df['close'] - 1
    # ... 156 more features
    return features

# Backtest with research implementation
features = compute_alpha158(data)
model.fit(features, labels)

# production/apps/signal_service/features.py
def compute_production_features(df):
    """Reimplemented Alpha158 for production. 500 lines of code."""
    features = pd.DataFrame()
    # KBAR features (slightly different!)
    features['KBAR_OPEN'] = (df['open'] - df['close']) / df['close']  # BUG: Formula wrong!
    features['KBAR_HIGH'] = df['high'] / df['close'] - 1
    # ... 156 more features
    return features
```

**Problems:**
- 1000 lines of duplicated code
- Bugs in one version don't get fixed in other
- Maintainence nightmare (fix bugs twice)
- Subtle formula differences cause train-serve skew

**With parity (GOOD):**
```python
# strategies/alpha_baseline/features.py (SINGLE SOURCE OF TRUTH)
def get_alpha158_features(
    symbols: List[str],
    start_date: str,
    end_date: str,
    data_dir: Path
) -> pd.DataFrame:
    """
    Compute Alpha158 features for given symbols and date range.

    This is the ONLY implementation. Used by both research and production
    to ensure feature parity (no train-serve skew).

    Returns:
        DataFrame with (date, instrument) MultiIndex and 158 feature columns
    """
    # Load data
    data = load_data(symbols, start_date, end_date, data_dir)

    # Compute KBAR features
    features = pd.DataFrame()
    features['KBAR_OPEN'] = data['open'] / data['close'] - 1
    features['KBAR_HIGH'] = data['high'] / data['close'] - 1
    # ... 156 more features

    return features

# research/notebooks/alpha_baseline_backtest.ipynb
from strategies.alpha_baseline.features import get_alpha158_features

features = get_alpha158_features(
    symbols=['AAPL', 'MSFT'],
    start_date='2020-01-01',
    end_date='2023-12-31',
    data_dir=Path('data/adjusted')
)
model.fit(features, labels)

# production/apps/signal_service/signal_generator.py
from strategies.alpha_baseline.features import get_alpha158_features

features = get_alpha158_features(
    symbols=request.symbols,
    start_date=request.date,
    end_date=request.date,
    data_dir=self.data_dir
)
predictions = self.model.predict(features)
```

**Benefits:**
- 500 lines instead of 1000 (DRY principle)
- Bug fix once, applies everywhere
- Guaranteed identical features
- Tests validate single implementation

### Example 2: Data Loading Parity

**Without parity (BAD):**
```python
# Research
def load_research_data(symbol):
    df = pd.read_csv(f'data/{symbol}.csv')
    df['date'] = pd.to_datetime(df['date'])
    return df.set_index('date')

# Production
def load_production_data(symbol):
    df = pd.read_parquet(f'data/{symbol}.parquet')  # Different format!
    return df  # Different index!
```

**Bug:** Parquet file has timestamp with milliseconds, CSV truncates to date. Index difference breaks downstream code.

**With parity (GOOD):**
```python
# libs/data/loader.py
class DataProvider:
    """Unified data loading for research and production."""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    def load(self, symbol: str, start: str, end: str) -> pd.DataFrame:
        """Load OHLCV data from Parquet files."""
        file_path = self.data_dir / f"{symbol}.parquet"
        df = pd.read_parquet(file_path)
        df['date'] = pd.to_datetime(df['date']).dt.date  # Normalize to date
        df = df.set_index(['date', 'symbol'])  # Standardize index
        mask = (df.index.get_level_values('date') >= start) & \
               (df.index.get_level_values('date') <= end)
        return df[mask]

# Both research and production
from libs.data.loader import DataProvider

provider = DataProvider(Path('data/adjusted'))
data = provider.load('AAPL', '2024-01-01', '2024-12-31')
```

### Example 3: Feature Normalization Parity

**Scenario:** Z-score normalization with online stats tracking.

**Without parity (BAD):**
```python
# Research: Compute stats on entire dataset (look-ahead bias!)
def normalize_research(features):
    return (features - features.mean()) / features.std()

# Production: Rolling window stats (correct, but different!)
def normalize_production(features):
    rolling_mean = features.rolling(252).mean()
    rolling_std = features.rolling(252).std()
    return (features - rolling_mean) / rolling_std
```

**Bug:** Research has look-ahead bias (using future data), production doesn't. Backtest is unrealistically good.

**With parity (GOOD):**
```python
# libs/features/normalization.py
def normalize_rolling(
    features: pd.DataFrame,
    window: int = 252,
    min_periods: int = 20
) -> pd.DataFrame:
    """
    Normalize features using rolling z-score.

    Avoids look-ahead bias by using only historical data for each point.

    Args:
        window: Rolling window size in periods (default 252 = 1 year)
        min_periods: Minimum periods required (default 20 = 1 month)
    """
    rolling_mean = features.rolling(window, min_periods=min_periods).mean()
    rolling_std = features.rolling(window, min_periods=min_periods).std()

    # Handle division by zero (constant features)
    rolling_std = rolling_std.replace(0, 1)

    return (features - rolling_mean) / rolling_std

# Both research and production
from libs.features.normalization import normalize_rolling

normalized = normalize_rolling(features, window=252, min_periods=20)
```

### Example 4: Testing Feature Parity

**How to verify parity is maintained:**

```python
# tests/test_feature_parity.py
import pytest
from strategies.alpha_baseline.features import get_alpha158_features
from apps.signal_service.signal_generator import SignalGenerator

class TestFeatureParity:
    """Ensure production uses same feature code as research."""

    def test_production_imports_research_features(self):
        """Verify SignalGenerator imports from strategies module."""
        import inspect
        import apps.signal_service.signal_generator as sg_module

        source = inspect.getsource(sg_module)

        # Must import from research code
        assert "from strategies.alpha_baseline.features import get_alpha158_features" in source

    def test_no_duplicate_implementations(self):
        """Verify no feature logic duplicated in signal service."""
        import inspect
        from apps.signal_service import signal_generator

        source = inspect.getsource(signal_generator)

        # Should NOT contain feature computation keywords
        assert "rolling" not in source.lower() or "import" in source
        assert "pct_change" not in source.lower() or "import" in source

    def test_same_inputs_produce_same_outputs(self):
        """Verify research and production produce identical features."""
        # Generate features using research code
        research_features = get_alpha158_features(
            symbols=['AAPL'],
            start_date='2024-01-01',
            end_date='2024-01-01',
            data_dir=Path('data/adjusted')
        )

        # Generate features using production code
        generator = SignalGenerator(model_registry, Path('data/adjusted'))
        production_signals = generator.generate_signals(
            symbols=['AAPL'],
            as_of_date=datetime(2024, 1, 1)
        )

        # Extract features from production (before model prediction)
        production_features = generator._get_features(['AAPL'], '2024-01-01')

        # Should be identical
        pd.testing.assert_frame_equal(research_features, production_features)
```

**Run parity tests in CI/CD:**
```bash
# Must pass before deploying to production
pytest tests/test_feature_parity.py -v
```

If test fails, either:
1. Research code changed but production didn't update (bad)
2. Production reimplemented features instead of importing (bad)
3. Library versions diverged (bad)

Fix before deploying!

## Further Reading

### Research Papers
- [Hidden Technical Debt in Machine Learning Systems](https://papers.nips.cc/paper/2015/file/86df7dcfd896fcaf2674f757a2463eba-Paper.pdf) (Google, 2015)
  - Section on train-serve skew
- [Machine Learning: The High-Interest Credit Card of Technical Debt](https://research.google/pubs/pub43146/) (Google, 2014)

### Industry Best Practices
- [Uber's Michelangelo ML Platform](https://eng.uber.com/michelangelo-machine-learning-platform/)
  - Feature store ensures parity
- [Airbnb's Zipline](https://medium.com/airbnb-engineering/zipline-airbnbs-machine-learning-data-management-platform-2c5b0c91afe8)
  - Training/serving pipeline
- [LinkedIn's Pro-ML](https://engineering.linkedin.com/blog/2019/01/productive-machine-learning)
  - Shared feature library

### Related Concepts
- **Feature Store**: Centralized system for feature computation and serving (Feast, Tecton)
- **Online/Offline Parity**: Batch (training) vs realtime (serving) feature consistency
- **Shadow Mode**: Run new features alongside old, compare outputs before switching
- **A/B Testing**: Gradually roll out new features to subset of traffic

### Tools and Frameworks
- **Feast**: Open-source feature store with parity guarantees
- **Tecton**: Commercial feature platform
- **MLflow**: Track which features used in each model version
- **DVC**: Version control for data and features

## Summary

**Feature parity is critical because:**
1. **Eliminates train-serve skew** - Model sees same features in production as training
2. **Reduces bugs** - Single implementation = single source of bugs
3. **Easier maintenance** - Bug fix once, applies everywhere
4. **Faster development** - No need to reimplement for production
5. **Higher confidence** - Backtest results transfer to live trading

**How to achieve feature parity:**
1. **Single codebase** - Research and production import from same module
2. **Shared environment** - Pin library versions in requirements.txt
3. **Explicit dependencies** - No hidden global state or config differences
4. **Integration tests** - Verify research and production produce identical outputs
5. **Code review** - Reject PRs that duplicate feature logic

**Red flags indicating parity violation:**
- ❌ Copy-pasted feature code
- ❌ "production version" of research function
- ❌ Different library versions
- ❌ Commented "TODO: make this match research"
- ❌ Integration tests disabled or skipped

**Feature parity is NOT optional.** It's the difference between a strategy that works in production and one that doesn't. Invest the time upfront to structure your code for parity - it pays back 10x in avoided bugs and maintenance costs.
