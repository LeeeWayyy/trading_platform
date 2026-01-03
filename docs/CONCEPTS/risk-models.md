# Risk Models

This document describes the multi-factor risk model implementation in the trading platform, based on the Barra methodology.

## Overview

The risk module provides quantitative risk analytics including:

- **Factor Covariance Estimation** (T2.2): Estimate factor return covariance matrices
- **Specific Risk Estimation** (T2.2): Estimate stock-level idiosyncratic variance
- **Portfolio Risk Decomposition** (T2.3): Decompose portfolio risk into factor and specific components
- **VaR and CVaR** (T2.3): Value at Risk and Expected Shortfall calculations

## Multi-Factor Risk Model Theory

### Portfolio Variance Formula

The Barra-style risk model decomposes portfolio variance into systematic (factor) and idiosyncratic (specific) components:

```
σ²_p = σ²_factor + σ²_specific
```

Where:

**Factor Variance:**
```
σ²_factor = f' × F × f
```
- `f = B' × w` is the K×1 portfolio factor exposure vector
- `B` is the N×K factor loadings matrix (stocks × factors)
- `F` is the K×K factor covariance matrix
- `w` is the N×1 portfolio weights vector

**Specific Variance:**
```
σ²_specific = Σ(w_i² × σ²_spec_i)
```
- `w_i` is the weight of stock i
- `σ²_spec_i` is the specific (idiosyncratic) variance of stock i

**Total Portfolio Risk:**
```
σ_p = √(σ²_factor + σ²_specific)
```

## Risk Contributions

### Marginal Contribution to Risk (MCTR)

MCTR measures how much risk changes when factor exposure changes:

```
MCTR_k = (F × f)_k / σ_p
```

This represents the sensitivity of portfolio volatility to changes in factor k exposure.

### Component Contribution to Risk (CCTR)

CCTR measures how much each factor contributes to total risk:

```
CCTR_k = f_k × MCTR_k
```

### Percent Contribution

The percentage of total risk attributable to factor k:

```
%Contrib_k = CCTR_k / σ_p
```

Note: Factor percent contributions sum to `σ²_factor / σ²_total`, not 1.0, because specific risk is not decomposed by factor.

## VaR and CVaR

### Value at Risk (VaR)

VaR estimates the maximum loss at a given confidence level, assuming normal distribution:

```
VaR_α = -μ + σ × z_α
```

Where:
- `μ` is the expected return (typically 0 for daily risk)
- `σ` is the daily portfolio standard deviation
- `z_α` is the standard normal quantile at confidence α

Common confidence levels:
- **95% VaR**: z_0.95 ≈ 1.645
- **99% VaR**: z_0.99 ≈ 2.326

### Conditional VaR (CVaR) / Expected Shortfall

CVaR is the expected loss given that loss exceeds VaR:

```
CVaR_α = -μ + σ × φ(z_α) / (1-α)
```

Where φ is the standard normal PDF.

**Key property:** CVaR ≥ VaR always (expected shortfall is worse than VaR threshold).

## Usage Example

```python
from libs.risk import (
    BarraRiskModel,
    RiskDecomposer,
    CovarianceResult,
    SpecificRiskResult,
)

# Create risk model from T2.2 outputs
model = BarraRiskModel.from_t22_results(
    covariance_result=cov_result,      # From FactorCovarianceEstimator
    specific_risk_result=spec_result,  # From SpecificRiskEstimator
    factor_loadings=loadings_df,       # permno, factor columns
)

# Define portfolio
portfolio = pl.DataFrame({
    "permno": [10001, 10002, 10003],
    "weight": [0.4, 0.35, 0.25],
})

# Compute risk decomposition
result = model.compute_portfolio_risk(portfolio, "my_portfolio")

print(f"Total Risk: {result.total_risk:.2%}")
print(f"Factor Risk: {result.factor_risk:.2%}")
print(f"Specific Risk: {result.specific_risk:.2%}")
print(f"95% VaR (daily): {result.var_95:.2%}")
print(f"95% CVaR (daily): {result.cvar_95:.2%}")

# Get factor contributions
contributions = model.compute_factor_contributions(portfolio)
print(contributions)
```

## Coverage Requirements

The risk model requires risk data (factor loadings + specific risk) for portfolio holdings. The `min_coverage` parameter controls minimum required coverage:

```python
config = BarraRiskModelConfig(min_coverage=0.8)  # Require 80% coverage
```

If coverage is below the threshold, `InsufficientCoverageError` is raised.

To check coverage before computation:

```python
coverage, missing_permnos = decomposer.check_portfolio_coverage(portfolio)
print(f"Coverage: {coverage:.1%}")
print(f"Missing: {len(missing_permnos)} stocks")
```

## Annualization

- Factor covariance (F) and specific variance are **daily** values
- Output risks (total, factor, specific) are **annualized** (× √252)
- VaR and CVaR are **daily** values (not annualized)

## Data Provenance

All results include `dataset_version_ids` for reproducibility:

```python
result.dataset_version_ids
# {'crsp_returns': 'abc123', 'compustat': 'v1.0.0', 'factor_loadings': 'def456'}
```

This enables point-in-time (PIT) correct backtesting and audit trails.

## Storage Schema

Results can be stored in parquet format matching the P4T2 task specification:

```python
portfolio_df, factor_df = result.to_storage_format()
portfolio_df.write_parquet("data/analytics/portfolio_risk.parquet")
factor_df.write_parquet("data/analytics/factor_contributions.parquet")
```

See `P4T2_TASK.md` for detailed schema definitions.

## Related Documentation

- [P4T2_TASK.md](../ARCHIVE/TASKS_HISTORY/P4T2_DONE.md): Task specification and schemas
- [covariance-estimation.md](./covariance-estimation.md): Factor covariance methodology
- [ADR-0021-risk-model-implementation.md](../ADRs/ADR-0021-risk-model-implementation.md): Architecture decisions (T2.4)
