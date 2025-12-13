# Market Microstructure Concepts

**Audience:** Beginner-to-intermediate quant traders and developers
**Purpose:** Educational guide to market microstructure analysis using high-frequency TAQ data
**Related Implementation:** `libs/analytics/microstructure.py`

---

## Table of Contents

1. [What is Market Microstructure?](#what-is-market-microstructure)
2. [TAQ Data Fundamentals](#taq-data-fundamentals)
3. [VPIN (Volume-Synchronized Probability of Informed Trading)](#vpin-volume-synchronized-probability-of-informed-trading)
4. [Realized Volatility](#realized-volatility)
5. [Spread and Depth Analysis](#spread-and-depth-analysis)
6. [Intraday Patterns](#intraday-patterns)
7. [Version Tracking and Reproducibility](#version-tracking-and-reproducibility)
8. [Implementation Details](#implementation-details)
9. [Best Practices](#best-practices)

---

## What is Market Microstructure?

**Market microstructure** studies how markets operate at the granular level - how orders are placed, matched, and executed, and what this reveals about market participants' behavior.

**Key Principle:** Price movements tell stories. Microstructure analysis helps decode those stories.

**Why It Matters for Trading:**
- **Detect informed trading** before price moves
- **Measure true liquidity** beyond bid-ask spread
- **Identify optimal execution times** during the trading day
- **Quantify market impact** of your orders

**Analogy:** If fundamental analysis is reading the news, and technical analysis is reading the chart, microstructure analysis is reading the order book - understanding who's buying and selling, and why.

---

## TAQ Data Fundamentals

### What is TAQ Data?

**TAQ (Trade and Quote)** data contains tick-by-tick records of every trade and every quote change in the market.

**Trade Records:**
```
timestamp           | symbol | price  | size | conditions
--------------------|--------|--------|------|------------
2024-01-15 09:30:01 | AAPL   | 185.50 | 100  | @
2024-01-15 09:30:01 | AAPL   | 185.51 | 200  | @
2024-01-15 09:30:02 | AAPL   | 185.49 | 50   | @
```

**Quote Records:**
```
timestamp           | symbol | bid    | bid_size | ask    | ask_size
--------------------|--------|--------|----------|--------|----------
2024-01-15 09:30:01 | AAPL   | 185.49 | 500      | 185.51 | 300
2024-01-15 09:30:01 | AAPL   | 185.48 | 800      | 185.51 | 300
2024-01-15 09:30:02 | AAPL   | 185.48 | 1000     | 185.52 | 400
```

### Key Fields

| Field | Description | Example |
|-------|-------------|---------|
| `timestamp` | Exact time (nanosecond precision) | `2024-01-15 09:30:01.123456789` |
| `price` | Trade execution price | `185.50` |
| `size` | Number of shares | `100` |
| `bid` | Best bid price | `185.49` |
| `ask` | Best ask price | `185.51` |
| `bid_size` | Shares available at bid | `500` |
| `ask_size` | Shares available at ask | `300` |

### Data Volume

For a liquid stock like AAPL on a typical day:
- **Trades:** 50,000 - 200,000 records
- **Quotes:** 500,000 - 2,000,000 records
- **Total:** Several GB of raw data per day

**Challenge:** Processing this volume requires efficient algorithms and proper data management.

---

## VPIN (Volume-Synchronized Probability of Informed Trading)

### What is VPIN?

**VPIN** estimates the probability that trading activity is driven by informed traders (those with private information) rather than noise traders.

**Key Insight:** When informed traders know something the market doesn't, they trade aggressively, creating detectable imbalances.

### The Intuition

**Normal Trading:**
```
Buy Volume:  ████████████  (50%)
Sell Volume: ████████████  (50%)
VPIN: Low (balanced)
```

**Informed Trading (before news):**
```
Buy Volume:  ████████████████████  (80%)
Sell Volume: ████                   (20%)
VPIN: High (imbalanced)
```

### BVC Classification (Bulk Volume Classification)

VPIN uses the **BVC method** to classify trades as buys or sells without relying on the Lee-Ready algorithm (which requires quote data matching).

**Formula:**
```python
# For each trade i:
r_i = log(P_i / P_{i-1})  # Log return
sigma = std(returns over lookback window)  # Ex-ante volatility

if sigma > 0:
    Z = r_i / sigma  # Standardized return
    V_buy = trade_size * Phi(Z)    # Phi = standard normal CDF
    V_sell = trade_size * (1 - Phi(Z))
else:
    # When sigma = 0 (no price variation), use neutral classification
    V_buy = trade_size / 2
    V_sell = trade_size / 2
```

**Why Log Returns?** Ensures dimensional consistency - both numerator and denominator are in log-return units.

**Why Ex-ante Sigma?** Uses only prior returns (excluding current trade) to avoid look-ahead bias and maintain independence during volatility shocks.

### Volume Buckets

VPIN operates on **volume buckets** - fixed-volume intervals rather than fixed-time intervals.

**Example (bucket_size = 10,000 shares):**
```
Time        Trade   Cumulative   Bucket
09:30:01    1,000   1,000        1
09:30:02    2,000   3,000        1
09:30:05    5,000   8,000        1
09:30:07    3,000   11,000       1 (overflow: 1,000 to bucket 2)
09:30:10    4,000   15,000       2 (remaining: 5,000 in bucket 2)
...
```

**Why Volume Buckets?**
- Volume is more informative than time
- Normalizes across high/low activity periods
- Better captures informed trading bursts

### VPIN Calculation

```python
# For each bucket b:
order_imbalance[b] = abs(V_buy[b] - V_sell[b])

# Rolling VPIN over n_buckets:
VPIN = mean(order_imbalance[last n_buckets]) / bucket_size
```

**Interpretation:**
- **VPIN = 0:** Perfect balance, no informed trading signal
- **VPIN = 0.5:** Moderate imbalance, some information asymmetry
- **VPIN = 1.0:** Maximum imbalance, strong informed trading signal

### Practical Application

**VPIN as Market Risk Indicator:**
- High VPIN often precedes large price moves
- Can trigger risk-off positioning
- Useful for options market makers

**Example:**
```
2024-01-15 14:30 - AAPL VPIN: 0.25 (normal)
2024-01-15 14:45 - AAPL VPIN: 0.45 (elevated)
2024-01-15 15:00 - AAPL VPIN: 0.65 (warning!)
2024-01-15 15:30 - AAPL announces surprise earnings revision
2024-01-15 15:35 - AAPL drops 5%
```

---

## Realized Volatility

### What is Realized Volatility?

**Realized Volatility (RV)** measures the actual variance of returns over a specific period, calculated from high-frequency returns.

**Key Principle:** High-frequency returns provide more accurate volatility estimates than daily returns.

### Why High-Frequency?

**Daily Returns (1 observation per day):**
```python
# Very noisy estimate
daily_return = (close - open) / open
daily_vol = std(daily_returns) * sqrt(252)  # Annualized
# Error: High (limited data)
```

**Intraday Returns (many observations per day):**
```python
# More precise estimate
5min_returns = [r1, r2, ..., r78]  # 78 five-minute bars
RV = sqrt(sum(r_i^2))  # Sum of squared returns
# Error: Lower (more data)
```

### RV Formula

**Daily Realized Volatility:**
```python
RV_t = sqrt(sum(r_{i,t}^2))
```

Where `r_{i,t}` are intraday returns at time i on day t.

**Annualized:**
```python
RV_annualized = RV_t * sqrt(252)
```

### Sampling Frequency Trade-off

| Frequency | Pros | Cons |
|-----------|------|------|
| 1 second | Maximum data | Microstructure noise |
| 5 minutes | Good balance | Standard choice |
| 30 minutes | Low noise | Limited observations |

**Our Default:** 5-minute sampling with at least 50 observations required.

### Implementation

```python
result = analyzer.compute_realized_volatility(
    symbol="AAPL",
    date=date(2024, 1, 15),
    sampling_freq=timedelta(minutes=5),
    min_observations=50
)

print(f"RV: {result.realized_vol:.4f}")
print(f"RV (annualized): {result.realized_vol_annualized:.4f}")
print(f"Observations: {result.n_observations}")
```

---

## Spread and Depth Analysis

### Bid-Ask Spread

**Definition:** The difference between the best ask (lowest sell price) and best bid (highest buy price).

**Formula:**
```python
spread = ask - bid
spread_bps = (spread / midpoint) * 10000  # In basis points
```

**Example:**
```
Bid: $185.49, Ask: $185.51
Spread: $0.02
Midpoint: $185.50
Spread (bps): 1.08 bps
```

### Why Spread Matters

**1. Transaction Cost**
```
Buy at ask:  $185.51
Sell at bid: $185.49
Round-trip cost: $0.02 per share (1.08 bps)
```

**2. Liquidity Indicator**
- Tight spread = High liquidity = Easy to trade
- Wide spread = Low liquidity = Costly to trade

**3. Information Content**
- Widening spread = Market makers see risk
- Narrowing spread = Confidence increasing

### Market Depth

**Definition:** The quantity of shares available at or near the best bid/ask.

**Level 1 Depth:**
```
Best Bid: 500 shares @ $185.49
Best Ask: 300 shares @ $185.51
```

**Why Depth Matters:**
- More depth = Can trade larger sizes without moving price
- Less depth = Price impact for larger orders

### Time-Weighted Calculations

Our implementation uses **time-weighted averages** for spread and depth:

```python
# Each quote record has a duration (time until next quote)
weighted_spread = sum(spread_i * duration_i) / total_duration
weighted_depth = sum(depth_i * duration_i) / total_duration
```

**Why Time-Weighted?**
- A quote that persists for 10 seconds matters more than one lasting 100 milliseconds
- Better represents the "available" liquidity over time

### Stale Quote Detection

Quotes become stale when market data stops updating:

```python
stale_threshold = timedelta(seconds=60)

# If no quote update for 60+ seconds, flag as stale
if time_since_last_quote > stale_threshold:
    stale_quote_count += 1
    stale_duration += duration
```

**Why It Matters:** Stale quotes indicate data quality issues or market disruptions.

---

## Intraday Patterns

### U-Shaped Volume Pattern

Most liquid stocks exhibit a **U-shaped** intraday volume pattern:

```
Volume
  |  *                                    *
  |   *                                  *
  |    *                                *
  |      * * * * * * * * * * * * * *  *
  |
  +-------------------------------------> Time
     09:30        12:00        15:30
```

**Why U-Shaped?**
- **Open (high):** Overnight information processed
- **Midday (low):** Lunch, fewer participants
- **Close (high):** End-of-day rebalancing, mutual fund flows

### Volatility Intraday Pattern

Volatility also exhibits predictable patterns:

```
Volatility
  |  *
  |   *
  |    * *
  |        * * * * * * * * * * * * * * *
  |                                     *
  +--------------------------------------> Time
     09:30        12:00           15:30
```

**Pattern:** High at open (overnight gap risk), declining through midday, slight pickup at close.

### Using Intraday Patterns

**Volume Pattern Analysis:**
```python
result = analyzer.analyze_intraday_pattern(
    symbol="AAPL",
    date=date(2024, 1, 15),
    time_bucket=timedelta(minutes=30)  # 30-minute buckets
)

# Returns volume, VWAP, trade count per bucket
for bucket in result.pattern_data:
    print(f"{bucket.time_start}: vol={bucket.volume}, vwap={bucket.vwap}")
```

**Applications:**
- **TWAP Execution:** Spread orders throughout day proportional to historical volume
- **Optimal Timing:** Execute during high-liquidity periods for lower impact
- **Anomaly Detection:** Unusual patterns may signal events

---

## Version Tracking and Reproducibility

### Why Version Tracking?

Research reproducibility requires knowing exactly which data produced which results.

**Problem Without Versioning:**
```
Q: "Why did my backtest results change?"
A: "Maybe the data was updated... maybe different snapshot... who knows?"
```

**Solution With Versioning:**
```
Q: "Why did my backtest results change?"
A: "Backtest v1 used data version abc123, backtest v2 used def456.
    The difference is corrected corporate action data for AAPL on 2024-01-10."
```

### Composite Version ID

When analysis uses multiple data sources, we create a **composite version ID**:

```python
# Multiple source versions
trade_version = "trades_v1.2.3_20240115"
quote_version = "quotes_v1.2.1_20240115"

# Composite version (deterministic)
composite = sha256(sorted([trade_version, quote_version]))[:32]
# Result: "a1b2c3d4e5f6..."
```

**Properties:**
- **Deterministic:** Same inputs always produce same composite ID
- **Unique:** Different data versions produce different composite IDs
- **Traceable:** Can reconstruct which versions were used

### Point-in-Time Enforcement

All queries enforce **single snapshot** consistency:

```python
# BAD: Could mix data from different snapshots
trades = get_trades(symbol="AAPL", snapshot="latest")
quotes = get_quotes(symbol="AAPL", snapshot="latest")  # Might be newer!

# GOOD: Same snapshot for all data
snapshot_id = "snap_20240115_120000"
trades = get_trades(symbol="AAPL", snapshot=snapshot_id)
quotes = get_quotes(symbol="AAPL", snapshot=snapshot_id)
```

---

## Implementation Details

### MicrostructureAnalyzer

The `MicrostructureAnalyzer` class provides a unified interface:

```python
from libs.analytics import MicrostructureAnalyzer

analyzer = MicrostructureAnalyzer(
    taq_provider=TAQLocalProvider(data_path="./data/taq")
)

# Realized Volatility
rv = analyzer.compute_realized_volatility(
    symbol="AAPL",
    date=date(2024, 1, 15)
)

# VPIN
vpin = analyzer.compute_vpin(
    symbol="AAPL",
    date=date(2024, 1, 15),
    bucket_size=10000,
    n_buckets=50
)

# Spread/Depth
spread = analyzer.compute_spread_depth_stats(
    symbol="AAPL",
    date=date(2024, 1, 15)
)

# Intraday Pattern
pattern = analyzer.analyze_intraday_pattern(
    symbol="AAPL",
    date=date(2024, 1, 15)
)
```

### Result Classes

All results include metadata for reproducibility:

```python
@dataclass
class VPINResult:
    vpin: float
    total_volume: int
    n_buckets_filled: int
    buy_volume: float
    sell_volume: float
    sigma_zero_bucket_fraction: float  # Fraction with sigma=0
    dataset_version_id: str  # For reproducibility
    calculation_timestamp: datetime
```

### Error Handling

The analyzer validates input data:

```python
# Insufficient data
if trade_count < min_observations:
    raise ValueError(f"Need at least {min_observations} observations")

# Invalid date
if date > datetime.now().date():
    raise ValueError("Cannot analyze future dates")

# Data quality issues
if stale_quote_ratio > 0.5:
    logger.warning("More than 50% stale quotes detected")
```

---

## Best Practices

### 1. Validate Data Quality First

```python
# Always check data before analysis
stats = analyzer.compute_spread_depth_stats(symbol, date)

if stats.stale_quote_count > 100:
    logger.warning("High stale quote count - check data feed")

if stats.n_quotes < 1000:
    logger.warning("Low quote count - may be illiquid period")
```

### 2. Use Appropriate Parameters

| Parameter | Liquid Stock (AAPL) | Illiquid Stock |
|-----------|---------------------|----------------|
| VPIN bucket_size | 10,000 shares | 1,000 shares |
| RV sampling_freq | 5 minutes | 15 minutes |
| Spread time_weight | Yes | Yes |

### 3. Handle Edge Cases

```python
# Market close - fewer observations
if market_hours < 4:
    min_observations = 20  # Lower threshold for half-days

# High volatility days - may need larger buckets
if historical_vol > 0.05:
    bucket_size = 20000  # Larger buckets for stability
```

### 4. Log Version IDs

```python
result = analyzer.compute_vpin(symbol, date)

logger.info(
    "VPIN computed",
    extra={
        "symbol": symbol,
        "date": str(date),
        "vpin": result.vpin,
        "dataset_version_id": result.dataset_version_id
    }
)
```

### 5. Monitor Sigma-Zero Buckets

```python
if result.sigma_zero_bucket_fraction > 0.1:
    logger.warning(
        "High sigma-zero fraction in VPIN",
        extra={
            "fraction": result.sigma_zero_bucket_fraction,
            "interpretation": "Many buckets with no price variation"
        }
    )
```

---

## Summary

**Key Takeaways:**

1. **Microstructure analysis** reveals market dynamics invisible to daily data
2. **VPIN** detects informed trading through volume imbalance (BVC method)
3. **Realized Volatility** provides accurate volatility from high-frequency returns
4. **Spread and Depth** measure true liquidity costs
5. **Intraday Patterns** guide optimal execution timing
6. **Version tracking** ensures research reproducibility
7. **Data quality** checks are essential before any analysis

**Remember:** Microstructure signals are probabilistic indicators, not crystal balls. Use them as one input among many in your trading decisions.

---

## Further Reading

- [Realized Volatility Concepts](./realized-volatility.md) - Deep dive into HAR models
- [Easley, Lopez de Prado, O'Hara (2012)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1695596) - Flow Toxicity and VPIN
- [Corsi (2009)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1090137) - HAR-RV Model
- [Andersen et al. (2003)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=602361) - Modeling and Forecasting Realized Volatility

---

**Last Updated:** 2025-12-07
**Related Task:** P4T2 Track 3 - T3.1 Microstructure Analytics
