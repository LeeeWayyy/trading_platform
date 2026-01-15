# Mean Reversion Trading Strategy

**Status:** In Development (P1T6)
**Version:** 0.1.0
**Created:** 2025-10-25

---

## Overview

Mean reversion is a trading strategy based on the statistical assumption that prices tend to revert to their historical mean over time. When prices deviate significantly from the mean (become overbought or oversold), they present profitable trading opportunities.

**Core Hypothesis:**
> Extreme price movements are temporary. Prices that move too far from their average will eventually return to that average.

This strategy complements the existing alpha_baseline strategy by providing diversification through a different trading approach (mean reversion vs. momentum/trend-following).

---

## Strategy Logic

### Entry Signals (BUY - Oversold)

Enter long positions when multiple indicators suggest oversold conditions:

1. **RSI < 30** - Relative Strength Index indicates oversold
2. **Bollinger %B < 0** - Price below lower Bollinger Band
3. **Z-Score < -2** - Price is 2+ standard deviations below mean
4. **Stochastic < 20** - Stochastic oscillator shows oversold
5. **Model Confidence > 0.6** - ML model predicts upward reversion

**Reasoning:** Multiple confirming indicators reduce false signals and improve success rate.

### Exit Signals (SELL - Overbought or Mean Reversion)

Exit long positions when:

1. **RSI > 70** - Relative Strength Index indicates overbought
2. **Bollinger %B > 1** - Price above upper Bollinger Band
3. **Z-Score > 0** - Price has reverted to mean
4. **Stop Loss Hit** - Position loss exceeds 5%
5. **Take Profit Hit** - Position gain exceeds 10%

**Reasoning:** Take profits when price reverts to mean or exits the oversold zone.

### Risk Management

- **Position Sizing:** Maximum 10% of portfolio per position
- **Stop Loss:** 5% maximum loss per trade
- **Take Profit:** 10% profit target
- **Diversification:** Typically 5-10 concurrent positions across uncorrelated symbols

---

## Technical Indicators

### 1. RSI (Relative Strength Index)

**Formula:**
```
RSI = 100 - (100 / (1 + RS))
where RS = Average Gain / Average Loss over 14 days
```

**Interpretation:**
- **RSI > 70:** Overbought (potential sell signal)
- **RSI < 30:** Oversold (potential buy signal)
- **RSI = 50:** Neutral (no clear signal)

**Parameters:**
- Period: 14 days (standard)
- Smoothing: Exponential (Wilder's method)

### 2. Bollinger Bands

**Formula:**
```
Middle Band = 20-day Simple Moving Average (SMA)
Upper Band = Middle Band + (2 × Standard Deviation)
Lower Band = Middle Band - (2 × Standard Deviation)
%B = (Price - Lower Band) / (Upper Band - Lower Band)
```

**Interpretation:**
- **Price touches upper band:** Overbought
- **Price touches lower band:** Oversold
- **Bands narrow (squeeze):** Low volatility, potential breakout ahead
- **Bands wide:** High volatility

**Parameters:**
- Period: 20 days
- Standard Deviations: 2.0

### 3. Stochastic Oscillator

**Formula:**
```
%K = 100 × (Close - Low14) / (High14 - Low14)
%D = 3-day SMA of %K
```

**Interpretation:**
- **%K and %D > 80:** Overbought
- **%K and %D < 20:** Oversold
- **%K crosses above %D:** Bullish signal
- **%K crosses below %D:** Bearish signal

**Parameters:**
- %K Period: 14 days
- %D Smoothing: 3 days

### 4. Z-Score

**Formula:**
```
Z-Score = (Price - Rolling Mean) / Rolling Std Dev
```

**Interpretation:**
- **Z > 2:** Price 2+ std devs above mean (overbought)
- **Z < -2:** Price 2+ std devs below mean (oversold)
- **-1 < Z < 1:** Price near mean (neutral)

**Parameters:**
- Period: 20 days

---

## Features

The strategy generates 11 features per symbol:

| Feature | Description | Range |
|---------|-------------|-------|
| `rsi` | Relative Strength Index | 0-100 |
| `bb_middle` | Bollinger Bands middle line (SMA) | Price units |
| `bb_upper` | Bollinger Bands upper band | Price units |
| `bb_lower` | Bollinger Bands lower band | Price units |
| `bb_width` | Band width (volatility measure) | 0-∞ |
| `bb_pct` | Percent B (price position in bands) | -0.5 to 1.5 |
| `stoch_k` | Stochastic %K (fast line) | 0-100 |
| `stoch_d` | Stochastic %D (slow line) | 0-100 |
| `price_zscore` | Z-Score of price | -3 to +3 |

**Feature Engineering:**
- All features computed using Polars for performance
- Rolling windows ensure no look-ahead bias
- Missing values handled via forward-fill
- Features normalized for ML model input

---

## Model Architecture

**Framework:** LightGBM (Gradient Boosting Decision Trees)

**Objective:** Regression (predict 1-5 day forward returns)

**Key Hyperparameters:**
- `num_leaves`: 31 (tree complexity)
- `learning_rate`: 0.05 (conservative for stability)
- `max_depth`: 7 (prevent overfitting)
- `feature_fraction`: 0.8 (random feature sampling)
- `bagging_fraction`: 0.8 (row sampling)
- `lambda_l1`: 0.1 (L1 regularization)
- `lambda_l2`: 0.1 (L2 regularization)

**Why LightGBM:**
- Fast training on large datasets
- Handles non-linear relationships
- Built-in regularization prevents overfitting
- Feature importance for interpretability

---

## Configuration

See `config.py` for all configurable parameters:

```python
from strategies.mean_reversion.config import MeanReversionConfig

config = MeanReversionConfig()

# Feature parameters
config.features.rsi_period = 14
config.features.bb_period = 20
config.features.bb_std = 2.0

# Model parameters
config.model.num_leaves = 31
config.model.learning_rate = 0.05

# Trading parameters
config.trading.rsi_oversold = 30.0
config.trading.rsi_overbought = 70.0
config.trading.stop_loss_pct = 0.05
```

---

## Usage

### 1. Feature Generation

```python
from strategies.mean_reversion.features import compute_mean_reversion_features
import polars as pl

# Load OHLCV data
prices = pl.read_parquet("data/adjusted/2024-01-01/AAPL.parquet")

# Compute features
features = compute_mean_reversion_features(prices)

# Access features
print(features["rsi"])
print(features["bb_pct"])
print(features["price_zscore"])
```

### 2. Signal Generation (Production)

```python
from strategies.mean_reversion.config import DEFAULT_CONFIG

# Check for oversold condition
if (
    features["rsi"][-1] < DEFAULT_CONFIG.trading.rsi_oversold
    and features["bb_pct"][-1] < DEFAULT_CONFIG.trading.bb_entry_threshold
    and features["price_zscore"][-1] < DEFAULT_CONFIG.trading.zscore_entry
):
    # BUY signal - oversold condition
    print("OVERSOLD: Enter long position")
```

### 3. Backtesting

```python
# TODO: Implement backtesting framework in P1T6 Component 4
```

---

## Performance Metrics (Target)

**Acceptance Criteria (from P1T6_PROGRESS.md):**
- ✅ Positive Sharpe Ratio (> 1.0 target)
- ✅ Positive Information Coefficient vs benchmark
- ✅ Maximum Drawdown < 20%
- ✅ Win Rate > 55%

**Metrics to Track:**
- Sharpe Ratio: Risk-adjusted returns
- Information Coefficient (IC): Prediction accuracy
- Maximum Drawdown: Worst peak-to-trough decline
- Win Rate: Percentage of profitable trades
- Average Win/Loss Ratio
- Calmar Ratio: Return / Max Drawdown

---

## Implementation Status

**Completed:**
- [x] Strategy directory structure
- [x] Feature engineering (`features.py`)
- [x] Configuration (`config.py`)
- [x] Documentation (`README.md`)

**In Progress:**
- [ ] Unit tests for features
- [ ] Model training pipeline
- [ ] Backtesting framework
- [ ] Integration with Signal Service

**Planned:**
- [ ] Walk-forward validation
- [ ] Parameter optimization
- [ ] Live paper trading validation
- [ ] Production deployment

---

## Trading Concepts Explained

### What is Mean Reversion?

Mean reversion is based on the statistical observation that extreme price movements tend to be temporary. Over time, prices tend to oscillate around a central value (the mean).

**Example:**
1. AAPL typically trades around $150 (mean)
2. News causes panic selling → drops to $130 (oversold)
3. Rational investors recognize the overreaction
4. Buying pressure increases → price reverts to $150

### When Does Mean Reversion Work?

**Best Conditions:**
- Range-bound markets (not trending)
- High liquidity stocks (less noise)
- Normal market conditions (not crisis)
- Short holding periods (1-5 days)

**Worst Conditions:**
- Strong trending markets (momentum dominates)
- Low liquidity stocks (wide bid-ask spreads)
- Market crises (correlations → 1)
- Structural changes (new fundamentals)

### Risk Considerations

**Risks:**
1. **False Signals:** Price continues in same direction ("catching a falling knife")
2. **Black Swan Events:** Extreme moves that don't revert
3. **Regime Changes:** Market structure shifts permanently
4. **Liquidity Risk:** Unable to exit positions quickly

**Mitigations:**
1. **Multiple Confirmations:** Require 3+ indicators to agree
2. **Stop Losses:** Cut losses quickly if wrong
3. **Position Sizing:** Limit exposure per trade
4. **Diversification:** Multiple uncorrelated positions

---

## References

**Academic Papers:**
- Avellaneda, M., & Lee, J. H. (2010). Statistical arbitrage in the US equities market
- Gatev, E., Goetzmann, W. N., & Rouwenhorst, K. G. (2006). Pairs trading: Performance of a relative-value arbitrage rule

**Books:**
- "Quantitative Trading" by Ernest Chan (Chapter 7: Mean Reversion)
- "Evidence-Based Technical Analysis" by David Aronson

**Online Resources:**
- [Investopedia: Mean Reversion](https://www.investopedia.com/terms/m/meanreversion.asp)
- [Investopedia: RSI](https://www.investopedia.com/terms/r/rsi.asp)
- [Investopedia: Bollinger Bands](https://www.investopedia.com/terms/b/bollingerbands.asp)

---

## Related Documentation

- [P1T6 Done](../../../docs/ARCHIVE/TASKS_HISTORY/P1T6_DONE.md) - Implementation complete
- [Baseline Strategy](../../../strategies/alpha_baseline/README.md) - Existing momentum strategy
- [Trading Concepts](../../../docs/CONCEPTS/) - Educational trading concepts
- [Coding Standards](../../../docs/STANDARDS/CODING_STANDARDS.md) - Code quality requirements

---

**Questions or Issues?**

Open an issue or see `/docs/GETTING_STARTED/` for project setup and contribution guidelines.
