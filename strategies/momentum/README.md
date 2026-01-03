# Momentum Trading Strategy

**Status:** In Development (P1T6)
**Version:** 0.1.0
**Created:** 2025-10-25

---

## Overview

Momentum trading follows the principle that "the trend is your friend" - assets exhibiting strong price trends will continue moving in the same direction, creating profitable opportunities.

**Core Hypothesis:**
> Strong trends persist. Assets with established upward momentum will continue rising; assets with downward momentum will continue falling.

This strategy complements mean_reversion by capitalizing on trending markets rather than reverting prices.

---

## Strategy Logic

### Entry Signals (BUY - Strong Uptrend)

Enter long positions when multiple indicators confirm strong bullish momentum:

1. **MA Golden Cross** - Fast MA crosses above slow MA (trend established)
2. **MACD Bullish Cross** - MACD line crosses above signal line
3. **ADX > 25** - Strong trend strength (not ranging market)
4. **ROC > 5%** - Positive 14-day momentum
5. **OBV Rising** - Volume confirms trend (accumulation)
6. **Model Confidence > 0.6** - ML model predicts trend continuation

**Reasoning:** Multiple confirming indicators reduce false signals and improve success rate.

### Exit Signals (SELL - Trend Exhaustion)

Exit long positions when:

1. **MACD Bearish Cross** - MACD crosses below signal line
2. **ADX Falling** - Trend strength weakening
3. **MA Death Cross** - Fast MA crosses below slow MA
4. **Stop Loss Hit** - Position loss exceeds 5%
5. **Take Profit Hit** - Position gain exceeds 15%

**Reasoning:** Exit when momentum weakens or profit targets met.

### Risk Management

- **Position Sizing:** Maximum 10% of portfolio per position
- **Stop Loss:** 5% maximum loss per trade
- **Take Profit:** 15% profit target (3:1 reward-risk ratio)
- **Trend Strength:** Only trade when ADX > 25 (strong trends)

---

## Technical Indicators

### 1. Moving Average Crossovers

**Formula:**
```
Fast MA = SMA(price, 10 days)
Slow MA = SMA(price, 50 days)
```

**Signals:**
- **Golden Cross:** Fast MA > Slow MA (bullish)
- **Death Cross:** Fast MA < Slow MA (bearish)

### 2. MACD (Moving Average Convergence Divergence)

**Formula:**
```
MACD Line = EMA(12) - EMA(26)
Signal Line = EMA(9) of MACD
Histogram = MACD - Signal
```

**Signals:**
- **Bullish Cross:** MACD crosses above signal
- **Bearish Cross:** MACD crosses below signal
- **Histogram > 0:** Bullish momentum
- **Histogram < 0:** Bearish momentum

### 3. ADX (Average Directional Index)

**Interpretation:**
- **ADX > 25:** Strong trend (good for momentum trading)
- **ADX < 20:** Weak/ranging market (avoid momentum trades)
- **+DI > -DI:** Uptrend direction
- **+DI < -DI:** Downtrend direction

### 4. Rate of Change (ROC)

**Formula:**
```
ROC = ((Price - Price_14_days_ago) / Price_14_days_ago) × 100
```

**Signals:**
- **ROC > 5%:** Strong positive momentum
- **ROC < -5%:** Strong negative momentum

### 5. On-Balance Volume (OBV)

**Interpretation:**
- **OBV Rising + Price Rising:** Bullish confirmation
- **OBV Falling + Price Falling:** Bearish confirmation
- **OBV/Price Divergence:** Potential trend reversal

---

## Features

The strategy generates 13 features per symbol:

| Feature | Description | Range |
|---------|-------------|-------|
| `ma_fast` | Fast moving average (10-day) | Price units |
| `ma_slow` | Slow moving average (50-day) | Price units |
| `ma_diff` | Fast - Slow MA (trend direction) | +/- |
| `ma_cross` | Crossover signal | -1/0/+1 |
| `macd_line` | MACD line | +/- |
| `macd_signal` | Signal line | +/- |
| `macd_hist` | Histogram (momentum) | +/- |
| `macd_cross` | MACD crossover signal | -1/0/+1 |
| `roc` | Rate of change (%) | +/- |
| `adx` | Trend strength | 0-100 |
| `plus_di` | Positive directional indicator | 0-100 |
| `minus_di` | Negative directional indicator | 0-100 |
| `obv` | On-balance volume | Cumulative |

---

## Model Architecture

**Framework:** LightGBM (Gradient Boosting Decision Trees)
**Objective:** Regression (predict forward returns during trends)

**Key Hyperparameters:**
- `num_leaves`: 31
- `learning_rate`: 0.05
- `max_depth`: 7
- `lambda_l1`: 0.1 (L1 regularization)
- `lambda_l2`: 0.1 (L2 regularization)

---

## Configuration

```python
from strategies.momentum.config import MomentumConfig

config = MomentumConfig()

# Feature parameters
config.features.ma_fast_period = 10
config.features.ma_slow_period = 50
config.features.adx_period = 14

# Trading parameters
config.trading.adx_threshold = 25.0
config.trading.roc_entry = 5.0
config.trading.stop_loss_pct = 0.05
```

---

## Usage

### Feature Generation

```python
from strategies.momentum.features import compute_momentum_features
import polars as pl

# Load OHLCV data
prices = pl.read_parquet("data/adjusted/2024-01-01/AAPL.parquet")

# Compute features
features = compute_momentum_features(prices)

# Access features
print(features["macd_hist"])
print(features["adx"])
print(features["ma_cross"])
```

### Signal Generation

```python
from strategies.momentum.config import DEFAULT_CONFIG

# Check for bullish momentum
if (
    features["ma_cross"][-1] == 1  # Golden cross
    and features["adx"][-1] > DEFAULT_CONFIG.trading.adx_threshold
    and features["roc"][-1] > DEFAULT_CONFIG.trading.roc_entry
    and features["macd_cross"][-1] == 1  # MACD bullish cross
):
    print("BULLISH MOMENTUM: Enter long position")
```

---

## When Does Momentum Work?

**Best Conditions:**
- Trending markets (up or down)
- High liquidity stocks
- Clear directional moves
- Strong catalysts (earnings, news)

**Worst Conditions:**
- Ranging/sideways markets
- High volatility with no clear trend
- Market reversals
- Low liquidity stocks

---

## Risk Considerations

**Risks:**
1. **False Breakouts:** Trend fails to continue (whipsaw)
2. **Late Entry:** Enter after trend is exhausted
3. **Trend Reversal:** Sudden direction change
4. **Overfitting:** Model too specific to historical patterns

**Mitigations:**
1. **Trend Strength Filter:** Only trade when ADX > 25
2. **Multiple Confirmations:** Require 3+ indicators to agree
3. **Stop Losses:** Cut losses quickly if wrong
4. **Position Sizing:** Limit exposure per trade

---

## Implementation Status

**Completed:**
- [x] Strategy directory structure
- [x] Feature engineering (`features.py`) - 5 indicators
- [x] Configuration (`config.py`)
- [x] Documentation (`README.md`)
- [x] Comprehensive test suite (28 tests passing)

**Next Steps:**
- [ ] Integrate with Signal Service
- [ ] Backtesting validation
- [ ] Walk-forward optimization
- [ ] Paper trading validation

---

## References

**Academic:**
- Jegadeesh, N., & Titman, S. (1993). Returns to Buying Winners and Selling Losers

**Books:**
- "Trend Following" by Michael Covel
- "Technical Analysis of the Financial Markets" by John Murphy

**Online:**
- [Investopedia: Momentum Trading](https://www.investopedia.com/terms/m/momentum.asp)
- [Investopedia: MACD](https://www.investopedia.com/terms/m/macd.asp)
- [Investopedia: ADX](https://www.investopedia.com/terms/a/adx.asp)

---

## Related Documentation

- [P1T6 Done](../../docs/ARCHIVE/TASKS_HISTORY/P1T6_DONE.md) - Implementation complete
- [Mean Reversion Strategy](../mean_reversion/README.md) - Complementary strategy
- [Coding Standards](../../docs/STANDARDS/CODING_STANDARDS.md)

---

**Questions or Issues?**

See `/docs/GETTING_STARTED/` for project setup and contribution guidelines.
