# risk

## Identity
- **Type:** Library
- **Port:** N/A
- **Container:** N/A

## Interface
### Public Interface (Exported Classes & Functions)
| Class/Function | Parameters | Returns | Description |
|----------------|------------|---------|-------------|
| `FactorCovarianceEstimator` | data, config | result | Estimate factor covariance matrices. |
| `SpecificRiskEstimator` | data | result | Estimate idiosyncratic risk. |
| `BarraRiskModel` | inputs, config | model | Multi-factor risk model. |
| `RiskDecomposer` | portfolio, risk model | result | Decompose portfolio risk (MCTR/CCTR). |
| `PortfolioOptimizer` | constraints, targets | result | Mean-variance optimization (optional). |
| `StressTester` | scenarios | result | Historical/hypothetical stress tests. |
| `CovarianceConfig` | fields | model | Config for covariance estimation. |
| `OptimizerConfig` | fields | model | Config for optimizer. |
| `StressScenario` | fields | model | Stress test scenario definition. |
| `compute_var_parametric` | returns, sigma | float | Parametric VaR. |
| `compute_cvar_parametric` | returns, sigma | float | Parametric CVaR. |

## Behavioral Contracts
### BarraRiskModel.fit(...)
**Purpose:** Fit a factor risk model from factor exposures and returns.

**Preconditions:**
- Factor exposures and returns aligned to same dates and symbols.

**Postconditions:**
- Produces factor covariance and specific risk estimates.

**Behavior:**
1. Estimate factor covariance via `FactorCovarianceEstimator`.
2. Estimate specific risk via `SpecificRiskEstimator`.
3. Assemble model with configuration metadata.

**Raises:**
- `InsufficientCoverageError`, `InsufficientDataError`.

### RiskDecomposer.decompose(...)
**Purpose:** Compute portfolio risk contributions by factor and asset.

**Preconditions:**
- Portfolio weights sum to 1 (or per config).

**Postconditions:**
- Returns `PortfolioRiskResult` with MCTR/CCTR.

**Behavior:**
1. Compute total risk from covariance.
2. Allocate contributions to factors/assets.

**Raises:**
- Value errors on invalid dimensions.

### Invariants
- Factor order is consistent with `CANONICAL_FACTOR_ORDER`.
- PIT correctness preserved via dataset version metadata.

### State Machine (if stateful)
```
[Inputs Ready] --> [Model Fit] --> [Risk Decomposed]
      |                  |
      +------------------+ (re-fit)
```
- **States:** inputs ready, model fit, risk decomposed.
- **Transitions:** re-fit when inputs change.

## Data Flow
```
returns + exposures -> covariance/specific risk -> risk model -> decomposition/optimization
```
- **Input format:** Time series returns, exposures, portfolio weights.
- **Output format:** Risk matrices and decomposition outputs.
- **Side effects:** None (pure computation).

## Usage Examples
### Example 1: Fit risk model
```python
model = BarraRiskModel(config)
result = model.fit(exposures, returns)
```

### Example 2: Decompose portfolio risk
```python
decomp = RiskDecomposer(model)
result = decomp.decompose(weights)
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Sparse coverage | few assets | `InsufficientCoverageError` |
| Missing optimizer deps | cvxpy not installed | Optimizer exports absent |
| Invalid constraints | infeasible | `InfeasibleOptimizationError` |

## Dependencies
- **Internal:** `libs.data_quality`, `libs.data_providers`
- **External:** numpy/pandas/scipy, optional cvxpy

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| N/A | - | - | Config passed via `CovarianceConfig`/`OptimizerConfig`. |

## Error Handling
- Typed errors for coverage, feasibility, and data insufficiency.

## Observability (Services only)
### Health Check
- **Endpoint:** N/A
- **Checks:** N/A

### Metrics
| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| N/A | - | - | Library has no metrics. |

## Security
- No secrets; inputs provided by callers.

## Testing
- **Test Files:** `tests/libs/risk/`
- **Run Tests:** `pytest tests/libs/risk -v`
- **Coverage:** N/A

## Related Specs
- `data_providers.md`
- `factors.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-09
- **Source Files:** `libs/risk/__init__.py`, `libs/risk/barra_model.py`, `libs/risk/factor_covariance.py`, `libs/risk/specific_risk.py`, `libs/risk/risk_decomposition.py`, `libs/risk/portfolio_optimizer.py`, `libs/risk/stress_testing.py`
- **ADRs:** N/A
