# Alpha158 Features

## Plain English Explanation

Alpha158 is a collection of 158 technical indicators (features) used to predict stock price movements. Think of it like having 158 different "lenses" to view price data - each lens captures a different pattern or signal.

**Simple analogy:** If you're trying to predict the weather, you might look at temperature, humidity, air pressure, wind speed, cloud cover, etc. Each measurement gives you a different piece of information. Alpha158 does the same thing for stock prices - it computes 158 different measurements from price and volume data.

## Why It Matters

Machine learning models need **features** (input variables) to make predictions. Raw OHLCV data (Open, High, Low, Close, Volume) isn't enough - we need to transform this data into meaningful patterns that capture:

1. **Trends** - Is the price going up or down?
2. **Momentum** - How fast is it moving?
3. **Volatility** - How much is it fluctuating?
4. **Mean reversion** - Is it far from its average?
5. **Volume patterns** - Are people buying or selling aggressively?

Alpha158 is a **battle-tested feature set** created by Microsoft Research. It's widely used in quantitative trading because it:
- ✅ Captures diverse price patterns
- ✅ Works across different time periods
- ✅ Combines well with machine learning models
- ✅ Has been validated on real market data

## The 6 Feature Categories

Alpha158 groups its 158 features into 6 categories:

### 1. KBAR (Candlestick Features) - 8 features

These capture basic price patterns from each day's candle:

- **Daily return**: `(close - close_yesterday) / close_yesterday`
  - Example: 0.02 means 2% gain today
- **Gap**: `(open - close_yesterday) / close_yesterday`
  - Example: 0.01 means opened 1% higher than yesterday's close
- **Intraday volatility**: `(high - low) / open`
  - Example: 0.05 means 5% swing from low to high
- **Intraday return**: `(close - open) / open`
  - Example: -0.01 means closed 1% below opening price
- **Upper shadow**: How much price rejected from the high
- **Lower shadow**: How much price rejected from the low
- **Close position**: Where close sits relative to high/low
- **True range ratio**: Total price movement relative to close

**Why it matters**: Candlestick patterns reveal intraday price action and trader sentiment.

### 2. KDJ (Stochastic Oscillator) - 6 features

Measures where current price sits relative to recent price range:

- **KDJ(6)**: Fast stochastic (6-day lookback)
- **KDJ(12)**: Medium stochastic (12-day lookback)
- **KDJ(24)**: Slow stochastic (24-day lookback)

Each KDJ generates 3 values:
- **K**: Current price position (0-100)
- **D**: Smoothed K (signal line)
- **J**: K acceleration (3*K - 2*D)

**Example**:
```
K = 80 → Price is near the high of recent range (overbought)
K = 20 → Price is near the low of recent range (oversold)
```

**Why it matters**: Identifies overbought/oversold conditions and momentum shifts.

### 3. RSI (Relative Strength Index) - 6 features

Measures momentum by comparing recent gains to recent losses:

- **RSI(6)**: Fast RSI (6-day lookback)
- **RSI(12)**: Medium RSI (12-day lookback)
- **RSI(24)**: Slow RSI (24-day lookback)

**Formula**: `RSI = 100 - (100 / (1 + avg_gain / avg_loss))`

**Example**:
```
RSI = 70 → Strong upward momentum (overbought)
RSI = 30 → Strong downward momentum (oversold)
RSI = 50 → Balanced (no clear momentum)
```

**Why it matters**: Detects trend strength and reversal points.

### 4. MACD (Moving Average Convergence Divergence) - 6 features

Captures trend changes by comparing fast and slow moving averages:

- **MACD(12,26,9)**: Standard MACD
  - **MACD line**: 12-day EMA - 26-day EMA
  - **Signal line**: 9-day EMA of MACD line
  - **Histogram**: MACD line - Signal line

**Interpretation**:
```
Histogram > 0 → Bullish (MACD above signal)
Histogram < 0 → Bearish (MACD below signal)
Histogram increasing → Momentum strengthening
Histogram decreasing → Momentum weakening
```

**Why it matters**: Identifies trend direction and momentum changes.

### 5. BOLL (Bollinger Bands) - 6 features

Measures how far current price deviates from its moving average:

- **(close - MA(20)) / StdDev(20)**: 20-day Bollinger position
- **(close - MA(60)) / StdDev(60)**: 60-day Bollinger position

**Example**:
```
Position = 2.0 → Price is 2 standard deviations above average (overbought)
Position = -2.0 → Price is 2 standard deviations below average (oversold)
Position = 0.0 → Price is at the moving average
```

**Why it matters**: Identifies volatility extremes and mean reversion opportunities.

### 6. ROC (Rate of Change) - 126 features

Measures percentage change over various time periods:

For each base feature (close, open, high, low, volume), compute:
- **Rate of change**: `(value - value_N_days_ago) / value_N_days_ago`
- **MA ratio**: `value / MA(value, N)`
- **Volatility ratio**: `StdDev(value, N) / value`

Time periods: 1, 3, 5, 10, 20, 30, 60 days

**Example** (Close price ROC):
```
ROC(close, 5) = (100 - 95) / 95 = 0.0526 → 5.26% gain over 5 days
ROC(close, 20) = (100 - 110) / 110 = -0.0909 → -9.09% loss over 20 days
```

**Why it matters**: Captures multi-timeframe trends and momentum.

## Feature Engineering Pipeline

Here's how raw OHLCV data becomes 158 features:

```
Raw Data (AAPL, 2024-01-01):
  open: 150.0
  high: 152.0
  low: 148.0
  close: 151.0
  volume: 1,000,000

↓ Feature Engineering

158 Features:
  KBAR0 (daily return): 0.0067
  KBAR1 (gap): 0.0000
  KBAR2 (intraday vol): 0.0267
  ...
  KDJ0 (K value, 6d): 65.3
  KDJ1 (D value, 6d): 58.7
  ...
  RSI0 (6d): 62.4
  RSI1 (12d): 58.9
  ...
  MACD0 (line): 0.32
  MACD1 (signal): 0.28
  MACD2 (histogram): 0.04
  ...
  BOLL0 (20d position): 1.2
  ...
  ROC0 (close, 1d): 0.0067
  ROC1 (close, 3d): 0.0234
  ...
  (158 total features)
```

## Normalization and Preprocessing

Raw features have different scales (RSI is 0-100, returns are -0.1 to 0.1). We normalize them:

### 1. Robust Z-Score Normalization
Uses median and MAD (median absolute deviation) instead of mean/std:

```python
normalized = (value - median) / MAD
```

**Why robust?** Outliers (e.g., earnings gaps) don't skew normalization.

**Example**:
```
Raw RSI values: [10, 20, 30, 40, 90]  # 90 is outlier
Median: 30, MAD: 10
Normalized: [-2.0, -1.0, 0.0, 1.0, 6.0]  # Outlier detected but not influencing median
```

### 2. Clipping Outliers
Values beyond ±3 MAD are clipped:

```python
if normalized > 3.0:
    normalized = 3.0
if normalized < -3.0:
    normalized = -3.0
```

**Why?** Prevents extreme values from dominating model training.

### 3. Forward Fill Missing Values
If a feature can't be computed (e.g., not enough history):

```python
# If RSI(20) needs 20 days of history
if days_available < 20:
    RSI = previous_valid_RSI  # Use last valid value
```

## Common Pitfalls

### 1. Look-Ahead Bias
**Problem:** Using future information in features.

**Example (WRONG)**:
```python
# This uses tomorrow's close price in today's feature!
feature = (close_tomorrow - close_today) / close_today
```

**Solution:**
```python
# Only use past/current information
feature = (close_today - close_yesterday) / close_yesterday
```

**In Alpha158:** All features use only past/current data. Labels (targets) are forward-looking.

### 2. Data Snooping
**Problem:** Normalizing using statistics from test set.

**Example (WRONG)**:
```python
# Compute mean/std from ALL data (2020-2024)
mean = all_data.mean()
std = all_data.std()
normalized = (test_data - mean) / std  # Using future info!
```

**Solution:**
```python
# Compute mean/std from TRAINING data only
mean = train_data.mean()
std = train_data.std()
normalized_test = (test_data - mean) / std  # No future info
```

**In Alpha158:** `fit_start_time` and `fit_end_time` define the period for computing normalization statistics.

### 3. Feature Explosion
**Problem:** Too many correlated features cause overfitting.

**Example:**
```
ROC(close, 1d) and ROC(close, 2d) are highly correlated
Adding both doesn't provide new information
```

**Solution:**
- Use feature selection (e.g., drop low-importance features)
- Use regularization (L1/L2 in model)
- Use tree-based models (LightGBM handles correlations well)

**In Alpha158:** 158 features is manageable for modern ML models, especially tree-based.

### 4. Non-Stationarity
**Problem:** Feature distributions change over time (market regimes shift).

**Example:**
```
2020-2021 (bull market): RSI typically 60-80
2022 (bear market): RSI typically 20-40
```

**Solution:**
- Cross-sectional normalization (normalize within each day across symbols)
- Retrain models periodically
- Use adaptive features (relative to recent history)

**In Alpha158:** CSZScoreNorm processor handles cross-sectional normalization during training.

## Examples

### Example 1: Computing KBAR Features by Hand

Given OHLCV data:
```
Date: 2024-01-02
Previous close: 150.0
Open: 151.0
High: 153.0
Low: 149.0
Close: 152.0
Volume: 1,000,000
```

Compute KBAR features:

1. **Daily return**: `(152 - 150) / 150 = 0.0133` (1.33% gain)
2. **Gap**: `(151 - 150) / 150 = 0.0067` (0.67% gap up)
3. **Intraday volatility**: `(153 - 149) / 151 = 0.0265` (2.65% range)
4. **Intraday return**: `(152 - 151) / 151 = 0.0066` (0.66% intraday gain)
5. **Upper shadow**: `(153 - 152) / 152 = 0.0066` (0.66% upper wick)
6. **Lower shadow**: `(151 - 149) / 151 = 0.0132` (1.32% lower wick)
7. **Close position**: `(2*152 - 153 - 149) / 151 = 0.0132`
8. **True range ratio**: `(153 - 149) / 152 = 0.0263`

### Example 2: Using Alpha158 in Code

```python
from strategies.alpha_baseline.features import get_alpha158_features, get_labels

# Get features for backtesting
features = get_alpha158_features(
    symbols=["AAPL", "MSFT", "GOOGL"],
    start_date="2024-01-01",
    end_date="2024-12-31",
    fit_start_date="2020-01-01",  # Use 2020-2023 for normalization
    fit_end_date="2023-12-31"
)

# Get labels (next-day returns)
labels = get_labels(
    symbols=["AAPL", "MSFT", "GOOGL"],
    start_date="2024-01-01",
    end_date="2024-12-31"
)

# Check structure
print(features.shape)  # (756, 158) - 252 days × 3 symbols, 158 features
print(labels.shape)    # (756, 1) - 252 days × 3 symbols, 1 label

# Example row
print(features.loc[("2024-01-01", "AAPL"), :5])
# KBAR0    0.0133
# KBAR1    0.0067
# KBAR2    0.0265
# KBAR3    0.0066
# KBAR4    0.0066
```

### Example 3: Feature Importance Analysis

After training a model, check which features matter most:

```python
import lightgbm as lgb
import pandas as pd

# Train model (simplified)
model = lgb.train(params, train_data)

# Get feature importance
importance = pd.DataFrame({
    'feature': features.columns,
    'importance': model.feature_importance()
}).sort_values('importance', ascending=False)

print(importance.head(10))

# Example output:
#     feature    importance
# 0   ROC_close_20   1543
# 1   RSI_12         1289
# 2   MACD_hist      1156
# 3   BOLL_20        1034
# 4   KDJ_K_12        987
# ...
```

**Insights:**
- ROC features (multi-timeframe momentum) are most important
- RSI and MACD (momentum indicators) are highly predictive
- KBAR features (single-day patterns) are less important

## Feature Parity Pattern

**Critical for production:** The same feature code must be used for:
1. **Research** (backtesting with historical data)
2. **Production** (real-time signal generation)

**Why?** If features are computed differently, the model won't perform as expected in production.

**Our implementation:**
```
strategies/alpha_baseline/features.py  ← Shared feature code
    ↓
Used by:
1. Research: Backtesting pipeline (this repo)
2. Production: Signal Service (deployed microservice)
```

**Deployment:**
- Signal Service imports `features.py` as a library
- Same Qlib expressions, same processors
- Guarantees train-serve consistency

## Further Reading

- [Qlib Alpha158 Documentation](https://qlib.readthedocs.io/en/latest/component/data.html#alpha158)
- [Technical Indicators Explained](https://www.investopedia.com/terms/t/technicalindicator.asp)
- [Feature Engineering for Time Series](https://towardsdatascience.com/feature-engineering-for-time-series-forecasting-8b43d9b5a2de)
- See `/docs/IMPLEMENTATION_GUIDES/t2-baseline-strategy-qlib.md` for full pipeline
- See `/docs/CONCEPTS/qlib-data-providers.md` for data integration
