# Factor Covariance Estimation

This document explains the factor covariance estimation methodology used in the trading platform's risk model.

## Overview

Factor covariance estimation is a core component of quantitative risk management. It enables:
- Portfolio risk decomposition (factor vs. specific risk)
- Mean-variance portfolio optimization
- Risk attribution analysis
- Stress testing with factor shocks

## Factor Return Extraction

### Cross-Sectional Regression

Daily factor returns are extracted using cross-sectional regression:

```
ret_i,t = alpha_t + sum_k(beta_k,t * exposure_i,k,t-1) + epsilon_i,t
```

Where:
- `ret_i,t` = Stock i's return on day t
- `alpha_t` = Cross-sectional intercept (market mean)
- `beta_k,t` = Factor k's return on day t (estimated coefficient)
- `exposure_i,k,t-1` = Stock i's exposure to factor k at t-1 (lagged for PIT correctness)
- `epsilon_i,t` = Idiosyncratic return

### WLS Weighting

We use Weighted Least Squares (WLS) with `sqrt(market_cap)` weights:

```python
weights = sqrt(market_cap)
```

**Rationale:** Larger stocks have more accurate prices and less microstructure noise. Square root weighting provides a balance between equal-weighting and pure value-weighting.

### Point-in-Time Correctness

Factor exposures are **lagged by one day** to prevent look-ahead bias:
- Returns at time t are regressed on exposures known at t-1
- This ensures the model only uses information available at decision time

## Exponential Decay Weighting

Recent observations are more relevant for forecasting. We apply exponential decay:

```
weight_t = exp(-ln(2) * age_t / halflife)
```

Where:
- `age_t` = Days between observation and as_of_date
- `halflife` = 60 days (configurable)

With a 60-day halflife:
- Yesterday's observation: weight ≈ 1.0
- 60 days ago: weight ≈ 0.5
- 120 days ago: weight ≈ 0.25

## Covariance Estimation Pipeline

The full pipeline applies multiple adjustments in sequence:

```
1. Raw Factor Returns    - From cross-sectional regression
2. Exponential Decay     - Weight recent observations more
3. Newey-West HAC        - Correct for autocorrelation
4. Ledoit-Wolf Shrinkage - Improve numerical stability
5. PSD Enforcement       - Ensure positive semi-definiteness
```

### Newey-West HAC Correction

Factor returns exhibit autocorrelation. The Newey-West heteroskedasticity and autocorrelation consistent (HAC) estimator corrects the covariance:

```python
# Bartlett kernel weights
for lag in range(1, n_lags + 1):
    bartlett_weight = 1 - lag / (n_lags + 1)
    hac_cov += bartlett_weight * (gamma_lag + gamma_lag.T)
```

Default: 5 lags (based on typical return autocorrelation persistence)

### Ledoit-Wolf Shrinkage

Sample covariance matrices are often ill-conditioned with noisy off-diagonal elements. Ledoit-Wolf shrinkage pulls the matrix toward a structured target:

```
shrunk_cov = (1 - alpha) * sample_cov + alpha * target
```

Where:
- `target` = Identity matrix scaled to preserve trace
- `alpha` = Optimal shrinkage intensity (computed by sklearn)

**Benefits:**
- Improved condition number
- Better out-of-sample performance
- Reduced estimation error

### PSD Enforcement

Numerical issues can produce matrices with small negative eigenvalues. We enforce positive semi-definiteness:

```python
def ensure_psd(cov):
    eigenvalues, eigenvectors = np.linalg.eigh(cov)
    eigenvalues_clipped = np.maximum(eigenvalues, 1e-10)
    return eigenvectors @ np.diag(eigenvalues_clipped) @ eigenvectors.T
```

## Specific Risk Estimation

Specific (idiosyncratic) risk is the variance not explained by factors:

```
specific_variance = total_variance - factor_variance
factor_variance = b' * Cov_factor * b
```

Where `b` is the stock's factor loading vector.

### Negative Variance Handling

If `specific_variance < 0` (due to estimation error), we floor it:

```python
if specific_variance < 0:
    specific_variance = 1e-8
    logger.warning("Floored negative specific variance")
```

### Annualization

Specific volatility is annualized:

```python
specific_vol = sqrt(specific_variance * 252)
```

## Usage Examples

### Computing Factor Covariance

```python
from libs.risk import FactorCovarianceEstimator, CovarianceConfig
from libs.models.factors import FactorBuilder

# Initialize
config = CovarianceConfig(halflife_days=60)
estimator = FactorCovarianceEstimator(factor_builder, config)

# Estimate covariance as of a specific date
result = estimator.estimate_covariance(as_of_date=date(2023, 6, 30))

# Access results
cov_matrix = result.factor_covariance  # 5x5 numpy array
factor_names = result.factor_names     # Canonical order
shrinkage = result.shrinkage_intensity
```

### Computing Specific Risk

```python
from libs.risk import SpecificRiskEstimator

# Initialize
specific_estimator = SpecificRiskEstimator(config, crsp_provider)

# Get factor loadings from FactorBuilder
factor_loadings = factor_builder.compute_all_factors(as_of_date).exposures

# Estimate specific risk
specific_result = specific_estimator.estimate(
    as_of_date=date(2023, 6, 30),
    factor_cov=result.factor_covariance,
    factor_loadings=factor_loadings,
)

# Access results
specific_risks = specific_result.specific_risks  # DataFrame
coverage = specific_result.coverage              # % of universe
```

### Portfolio Risk Decomposition

```python
# Portfolio weights (vector, sums to 1)
w = np.array([0.1, 0.2, 0.3, 0.25, 0.15])

# Factor exposures for portfolio stocks (N x K matrix)
B = factor_loadings_matrix

# Portfolio factor exposure
portfolio_exposure = w @ B  # K-vector

# Factor contribution to portfolio variance
factor_var = portfolio_exposure @ cov_matrix @ portfolio_exposure

# Specific contribution (weighted specific variances)
specific_var = w @ (specific_variances * w)

# Total portfolio variance
total_var = factor_var + specific_var
portfolio_vol = np.sqrt(total_var * 252)  # Annualized
```

## Canonical Factor Ordering

All covariance matrices use this canonical ordering:

1. `momentum_12_1` - 12-1 Momentum
2. `book_to_market` - Value (Book-to-Market)
3. `roe` - Quality (Return on Equity)
4. `log_market_cap` - Size
5. `realized_vol` - Low Volatility

This ensures consistent matrix indexing across the system.

## Configuration Options

| Parameter | Default | Description |
|-----------|---------|-------------|
| `halflife_days` | 60 | Exponential decay half-life |
| `min_observations` | 126 | Minimum trading days required |
| `newey_west_lags` | 5 | HAC lag parameter |
| `shrinkage_intensity` | None | None = Ledoit-Wolf optimal (uses scaled identity target) |
| `min_stocks_per_day` | 100 | Minimum stocks for valid regression |
| `lookback_days` | 252 | Calendar days for factor return calculation |

## Storage Schema

Results are stored in parquet with the following schema:

**Factor Returns:**
```sql
date DATE, factor_name VARCHAR, daily_return DOUBLE,
t_statistic DOUBLE, r_squared DOUBLE, dataset_version_id VARCHAR
```

**Covariance Matrix:**
```sql
as_of_date DATE, factor_i VARCHAR, factor_j VARCHAR,
covariance DOUBLE, correlation DOUBLE, halflife_days INT,
shrinkage_intensity DOUBLE, dataset_version_id VARCHAR
```

**Specific Risk:**
```sql
as_of_date DATE, permno INT, specific_variance DOUBLE,
specific_vol DOUBLE, dataset_version_id VARCHAR
```

## References

- Ledoit, O., & Wolf, M. (2004). "A well-conditioned estimator for large-dimensional covariance matrices"
- Newey, W. K., & West, K. D. (1987). "A simple, positive semi-definite, heteroskedasticity and autocorrelation consistent covariance matrix"
- Barra Risk Model Handbook (for factor model methodology)
