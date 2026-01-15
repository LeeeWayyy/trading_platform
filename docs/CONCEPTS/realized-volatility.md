# Realized Volatility and HAR Forecasting

**Audience:** Beginner-to-intermediate quant traders and developers
**Purpose:** Educational guide to realized volatility measurement and HAR forecasting models
**Related Implementation:** `libs/analytics/volatility.py`

---

## Table of Contents

1. [What is Realized Volatility?](#what-is-realized-volatility)
2. [Why High-Frequency Data?](#why-high-frequency-data)
3. [Calculating Realized Volatility](#calculating-realized-volatility)
4. [The HAR Model](#the-har-model)
5. [HAR Feature Construction](#har-feature-construction)
6. [Model Fitting and Forecasting](#model-fitting-and-forecasting)
7. [Avoiding Look-Ahead Bias](#avoiding-look-ahead-bias)
8. [Practical Applications](#practical-applications)
9. [Best Practices](#best-practices)

---

## What is Realized Volatility?

**Realized Volatility (RV)** is a measure of actual price variation observed over a specific period, calculated from high-frequency return data.

**Key Distinction:**
- **Implied Volatility:** What the market expects (from options prices)
- **Realized Volatility:** What actually happened (from historical prices)

**Why It Matters:**
- Options pricing (compare RV to IV for trading opportunities)
- Risk management (position sizing based on actual risk)
- Portfolio optimization (volatility-weighted allocation)
- Volatility forecasting (predict future risk)

**Analogy:** If implied volatility is the weather forecast, realized volatility is the actual temperature recorded yesterday.

---

## Why High-Frequency Data?

### The Problem with Daily Returns

Using daily close-to-close returns to estimate volatility is inefficient:

```python
# Daily returns over 20 days
daily_returns = [0.01, -0.02, 0.005, ...]  # 20 observations

# Variance estimate (unbiased)
variance = sum((r - mean)^2) / (n - 1)
std_error = variance / sqrt(2 * n)  # Very high with n=20!
```

**Problem:** With only 20 observations per month, the estimation error is huge.

### The High-Frequency Solution

Intraday returns provide many more observations:

```python
# 5-minute returns over 1 day
intraday_returns = [r1, r2, ..., r78]  # 78 observations per day!

# Sum of squared returns
RV = sum(r_i^2)  # No mean subtraction needed
```

**Benefit:** 78 observations per day (5-min sampling, 6.5 hours) vs. 1 observation per day.

### Theory: Quadratic Variation

Under continuous-time asset price models, as sampling frequency increases:

```
sum(r_i^2) → Integrated Variance (true volatility)
```

This is the **quadratic variation** of the price process.

**In Practice:** We can't sample infinitely fast due to:
- **Microstructure noise:** Bid-ask bounce, discreteness
- **Data limitations:** Storage, processing costs

**Optimal Frequency:** 5-minute sampling balances precision and noise (Andersen et al., 2003).

---

## Calculating Realized Volatility

### Basic Formula

**Daily Realized Volatility:**
```python
RV_t = sqrt(sum(r_{i,t}^2))
```

Where `r_{i,t}` are intraday returns on day t.

**Example:**
```python
# 5-minute log returns for AAPL on 2024-01-15
returns = [0.001, -0.002, 0.0005, ...]  # 78 values

# Sum of squared returns
sum_sq = sum(r^2 for r in returns)
# sum_sq = 0.000001 + 0.000004 + 0.00000025 + ... = 0.0002

# Daily RV
rv_daily = sqrt(0.0002) = 0.0141 (1.41%)
```

### Annualization

To compare with annual volatility metrics:

```python
RV_annualized = RV_daily * sqrt(252)

# Example:
# RV_daily = 0.0141
# RV_annualized = 0.0141 * sqrt(252) = 0.224 (22.4%)
```

**Note:** 252 is the typical number of trading days per year.

### Sampling Frequency Choice

| Frequency | Observations/Day | Pros | Cons |
|-----------|------------------|------|------|
| 1 second | ~23,400 | Maximum data | Microstructure noise |
| 1 minute | ~390 | High precision | Some noise |
| **5 minutes** | ~78 | **Good balance** | **Standard choice** |
| 15 minutes | ~26 | Low noise | Fewer observations |
| 30 minutes | ~13 | Very low noise | May miss patterns |

**Our Default:** 5-minute sampling, minimum 50 observations.

---

## The HAR Model

### Introduction

The **Heterogeneous Autoregressive (HAR)** model, introduced by Corsi (2009), forecasts future realized volatility using past volatility at multiple time horizons.

**Key Insight:** Different market participants operate on different time scales:
- **High-frequency traders:** React to daily volatility
- **Institutional investors:** React to weekly patterns
- **Long-term investors:** React to monthly trends

### HAR-RV Specification

```
RV_{t+h} = c + b_d * RV_d + b_w * RV_w + b_m * RV_m + error
```

Where:
- `RV_{t+h}` = Realized volatility h days ahead (target)
- `c` = Intercept
- `RV_d` = Daily component (lag-1 RV)
- `RV_w` = Weekly component (average of lags 1-5)
- `RV_m` = Monthly component (average of lags 1-22)
- `b_d, b_w, b_m` = Regression coefficients

### Why HAR Works

**1. Captures Persistence**
Volatility is highly persistent - high volatility tends to follow high volatility.

```
High RV today → Higher RV forecast tomorrow
```

**2. Captures Multi-Scale Dynamics**
Different horizons capture different information:

```
RV_d: Overnight gap risk, immediate news
RV_w: Earnings cycle, weekly patterns
RV_m: Market regime, macro conditions
```

**3. Simple Yet Effective**
Despite its simplicity (just 4 parameters), HAR often outperforms complex models like GARCH.

---

## HAR Feature Construction

### Lag Specification

**Critical:** All lags must exclude RV_t to prevent look-ahead bias.

```python
# At time t, predicting RV_{t+h}:

RV_d[t] = RV[t-1]                    # Yesterday's RV
RV_w[t] = mean(RV[t-5], ..., RV[t-1])   # Average of lags 1-5
RV_m[t] = mean(RV[t-22], ..., RV[t-1])  # Average of lags 1-22
```

**Visual Timeline:**
```
t-22  t-21  ...  t-5  t-4  t-3  t-2  t-1  t  t+h
 |__________________|    |____|    |    |    |
      Monthly avg        Weekly     Daily Target
                          avg
```

### Implementation Detail

```python
def _construct_har_features(self, rv_values: np.ndarray) -> dict:
    """Construct HAR features with proper lagging."""
    n = len(rv_values)

    rv_d = np.full(n, np.nan)
    rv_w = np.full(n, np.nan)
    rv_m = np.full(n, np.nan)
    rv_target = np.full(n, np.nan)

    for t in range(22, n - self.horizon):
        # Lag-1 (yesterday)
        rv_d[t] = rv_values[t - 1]

        # Weekly average (lags 1-5)
        rv_w[t] = np.mean(rv_values[t - 5 : t])

        # Monthly average (lags 1-22)
        rv_m[t] = np.mean(rv_values[t - 22 : t])

        # Target (h days ahead)
        rv_target[t] = rv_values[t + self.horizon]

    return {"rv_d": rv_d, "rv_w": rv_w, "rv_m": rv_m, "rv_target": rv_target}
```

**Note:** Features start at t=22 because monthly lag needs 22 prior observations.

---

## Model Fitting and Forecasting

### OLS Estimation

HAR parameters are estimated using Ordinary Least Squares:

```python
# Design matrix X: [1, RV_d, RV_w, RV_m]
# Target vector y: RV_target

coefficients = np.linalg.lstsq(X, y, rcond=None)[0]

# coefficients = [intercept, b_d, b_w, b_m]
```

**Why OLS?**
- Simple, deterministic, numerically stable
- Closed-form solution (no iterative optimization)
- Sufficient for point forecasts (no standard errors needed)

### R-Squared Interpretation

```python
y_pred = X @ coefficients
ss_res = sum((y - y_pred)^2)
ss_tot = sum((y - mean(y))^2)
r_squared = 1 - (ss_res / ss_tot)
```

**Typical Values:**
- R² = 0.3-0.5: Good for volatility forecasting
- R² > 0.5: Excellent (volatility is predictable!)
- R² < 0.2: Model may not capture dynamics well

### Generating Forecasts

```python
# Given recent RV data:
rv_d = current_rv[-1]           # Latest RV
rv_w = mean(current_rv[-5:])    # Average of last 5
rv_m = mean(current_rv[-22:])   # Average of last 22

# Point forecast:
forecast = intercept + b_d*rv_d + b_w*rv_w + b_m*rv_m

# Ensure non-negative (volatility can't be negative)
forecast = max(0, forecast)
```

### Code Example

```python
from libs.platform.analytics import HARVolatilityModel
import polars as pl

# Load historical RV data
rv_data = pl.DataFrame({
    "date": [...],  # 100+ dates
    "rv": [...]     # Corresponding RV values
})

# Initialize and fit
model = HARVolatilityModel(forecast_horizon=1)
fit_result = model.fit(rv_data, dataset_version_id="rv_v1.0")

print(f"Coefficients:")
print(f"  Intercept: {fit_result.intercept:.6f}")
print(f"  Daily:     {fit_result.coef_daily:.4f}")
print(f"  Weekly:    {fit_result.coef_weekly:.4f}")
print(f"  Monthly:   {fit_result.coef_monthly:.4f}")
print(f"R-squared:   {fit_result.r_squared:.4f}")

# Generate forecast
recent_rv = rv_data.tail(22)  # Need 22 days for monthly lag
forecast = model.forecast(recent_rv)

print(f"Forecast RV: {forecast.rv_forecast:.4f}")
print(f"Forecast RV (annualized): {forecast.rv_forecast_annualized:.2%}")
```

---

## Avoiding Look-Ahead Bias

### What is Look-Ahead Bias?

**Look-ahead bias** occurs when future information leaks into features used for prediction.

**Example of Bias:**
```python
# WRONG: Using RV[t] to predict RV[t+1]
RV_d[t] = RV[t]  # Includes today's RV, which we don't know yet!
```

**Correct:**
```python
# RIGHT: Using RV[t-1] to predict RV[t+1]
RV_d[t] = RV[t-1]  # Only uses past information
```

### Why It Matters

In backtesting, look-ahead bias makes your model appear better than it actually is:

```
Backtest with bias:     R² = 0.80 (looks amazing!)
Live trading:           R² = 0.30 (reality check)
```

### Our Safeguards

**1. Lag Construction**
All HAR features use strictly past values:
```python
rv_d[t] = rv_values[t - 1]      # Not t!
rv_w[t] = np.mean(rv_values[t - 5 : t])  # Excludes t
rv_m[t] = np.mean(rv_values[t - 22 : t])  # Excludes t
```

**2. Target Construction**
Target is future RV:
```python
rv_target[t] = rv_values[t + horizon]  # Future value
```

**3. Minimum Data Requirements**
Require at least 60 observations to ensure meaningful estimation:
```python
if realized_vol.height < 60:
    raise ValueError("Minimum 60 observations required")
```

---

## Practical Applications

### 1. Options Trading

**Volatility Arbitrage:**
```python
implied_vol = 0.25  # From option prices
forecast_rv = model.forecast(recent_rv).rv_forecast_annualized

if implied_vol > forecast_rv * 1.2:  # IV 20% higher than expected RV
    # Sell options (short volatility)
    pass
elif implied_vol < forecast_rv * 0.8:  # IV 20% lower than expected RV
    # Buy options (long volatility)
    pass
```

### 2. Position Sizing

**Volatility-Adjusted Sizing:**
```python
target_risk = 0.02  # 2% daily risk target
forecast_rv = model.forecast(recent_rv).rv_forecast

position_size = target_risk / forecast_rv
# Higher forecast RV → Smaller position
```

### 3. Risk Management

**Dynamic Stop-Losses:**
```python
forecast_rv = model.forecast(recent_rv).rv_forecast

# Set stop-loss based on expected volatility
stop_distance = 2 * forecast_rv  # 2 sigma move
entry_price = 100.0
stop_price = entry_price * (1 - stop_distance)
```

### 4. Portfolio Allocation

**Inverse Volatility Weighting:**
```python
forecasts = {
    "AAPL": model_aapl.forecast(rv_aapl).rv_forecast,
    "MSFT": model_msft.forecast(rv_msft).rv_forecast,
    "GOOGL": model_googl.forecast(rv_googl).rv_forecast,
}

# Inverse volatility weights
inv_vol = {k: 1/v for k, v in forecasts.items()}
total = sum(inv_vol.values())
weights = {k: v/total for k, v in inv_vol.items()}

# Lower volatility stocks get higher weight
```

---

## Best Practices

### 1. Data Quality Checks

```python
# Check for gaps
dates = rv_data["date"].to_list()
for i in range(1, len(dates)):
    gap = (dates[i] - dates[i-1]).days
    if gap > 5:  # More than a week gap
        logger.warning(f"Large gap detected: {gap} days at {dates[i]}")

# Check for outliers
rv_values = rv_data["rv"].to_numpy()
median_rv = np.median(rv_values)
outliers = rv_values > median_rv * 10  # 10x median
if np.any(outliers):
    logger.warning(f"Potential outliers: {sum(outliers)} observations")
```

### 2. NaN Handling

Our implementation forward-fills NaN values up to 5 consecutive:

```python
# Acceptable: 1-5 consecutive NaNs (e.g., holiday weeks)
# Rejected: >5 consecutive NaNs (indicates data issues)

rv_filled = model._forward_fill_nan(rv_values, max_consecutive=5)
```

### 3. Model Diagnostics

```python
result = model.fit(rv_data, version_id)

# Check R-squared
if result.r_squared < 0.2:
    logger.warning("Low R-squared - model may not capture dynamics")

# Check coefficient signs
if result.coef_daily < 0:
    logger.warning("Negative daily coefficient - unusual")

# Check observation count
if result.n_observations < 100:
    logger.warning("Limited observations - estimates may be unstable")
```

### 4. Out-of-Sample Testing

**Never trust in-sample R² alone:**

```python
# Split data
train = rv_data.head(200)
test = rv_data.tail(50)

# Fit on train
model.fit(train, "train_version")

# Evaluate on test
errors = []
for i in range(22, len(test)):
    forecast = model.forecast(test[:i])
    actual = test["rv"][i]
    errors.append((forecast.rv_forecast - actual) ** 2)

rmse = sqrt(mean(errors))
print(f"Out-of-sample RMSE: {rmse:.4f}")
```

### 5. Regime Awareness

HAR coefficients may change across market regimes:

```python
# Fit separate models for different regimes
low_vol_data = rv_data.filter(pl.col("rv") < 0.015)
high_vol_data = rv_data.filter(pl.col("rv") >= 0.015)

model_low = HARVolatilityModel()
model_low.fit(low_vol_data, "low_vol")

model_high = HARVolatilityModel()
model_high.fit(high_vol_data, "high_vol")

# Compare coefficients - they often differ!
```

---

## Summary

**Key Takeaways:**

1. **Realized Volatility** measures actual price variation from high-frequency data
2. **5-minute sampling** balances precision and noise
3. **HAR model** captures multi-scale volatility dynamics (daily, weekly, monthly)
4. **Proper lagging** is critical - all features must use only past information
5. **OLS estimation** is simple and sufficient for point forecasts
6. **R² of 0.3-0.5** is typical and useful for volatility forecasting
7. **Out-of-sample testing** is essential for realistic performance estimates

**The HAR Advantage:**
- Simple (4 parameters) yet effective
- Interpretable (each component has economic meaning)
- Robust (OLS is numerically stable)
- Widely used in academic research and industry

---

## Further Reading

- [Corsi (2009)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=1090137) - Original HAR-RV paper
- [Andersen et al. (2003)](https://www.nber.org/system/files/working_papers/w10674/w10674.pdf) - Realized volatility theory
- [Microstructure Concepts](./microstructure.md) - Related high-frequency analysis
- [Risk Management Concepts](./risk-management.md) - Using volatility in risk controls

---

**Last Updated:** 2025-12-07
**Related Task:** P4T2 Track 3 - T3.1 Microstructure Analytics
