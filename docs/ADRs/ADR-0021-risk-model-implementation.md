# ADR-0021: Risk Model Implementation

## Status
Accepted

## Date
2025-12-07

## Context

The trading platform requires production-ready risk analytics for portfolio management, risk decomposition, and stress testing. Key requirements include:

1. **Multi-factor risk model** for systematic risk analysis
2. **Portfolio optimization** with constraints (min-variance, max-Sharpe, risk parity)
3. **Stress testing** for scenario analysis (historical + hypothetical)
4. **Integration** with existing factor infrastructure (T2.1) and covariance estimation (T2.2)

We evaluated several approaches:

### Option 1: Third-party Risk Platform (e.g., Axioma, Barra)
- **Pros:** Production-tested, full feature set
- **Cons:** Expensive licensing, vendor lock-in, limited customization

### Option 2: Open-source Libraries (e.g., PyPortfolioOpt, riskfolio-lib)
- **Pros:** Free, community-maintained
- **Cons:** May not integrate well with our factor model, limited stress testing

### Option 3: Custom Implementation
- **Pros:** Full control, direct integration with our factor infrastructure
- **Cons:** Development effort, requires careful validation

## Decision

We implement a **custom Barra-style risk model** with the following architecture:

### 1. Layered Design (T2.1 → T2.2 → T2.3 → T2.4)

```
T2.1: Factor Construction       → Factor exposures (B matrix)
      ├── momentum_12_1, book_to_market, roe, log_market_cap, realized_vol

T2.2: Covariance Estimation     → Factor covariance (F matrix) + Specific risk (D matrix)
      ├── Ledoit-Wolf shrinkage for F
      └── Newey-West adjustment for D

T2.3: Risk Model Assembly       → BarraRiskModel with compute_portfolio_risk()
      └── Portfolio variance: σ² = w' @ B @ F @ B' @ w + w' @ D @ w

T2.4: Optimizer & Stress Test   → PortfolioOptimizer, StressTester
      ├── cvxpy-based optimization
      └── Historical + hypothetical scenarios
```

### 2. Covariance Matrix Construction

The full N×N asset covariance matrix is constructed from the Barra model:

```python
Σ = B @ F @ B.T + D

Where:
- B = N×K factor loadings matrix (from T2.1)
- F = K×K factor covariance matrix (from T2.2)
- D = N×N diagonal specific variance matrix (from T2.2)
```

### 3. Portfolio Optimizer (cvxpy)

Four optimization objectives supported:

| Objective | Formulation | Transaction Costs |
|-----------|-------------|-------------------|
| **Min-Variance** | minimize w'Σw + λ\|\|w - w₀\|\|₁ | Yes (in objective) |
| **Max-Sharpe** | Efficient frontier search with return targets | No (non-convex) |
| **Mean-Variance-Cost** | maximize μ'w - (γ/2)w'Σw - costs | Yes (in objective) |
| **Risk Parity** | Iterative ERC algorithm | No |

**Solver Strategy:**
- Primary: CLARABEL (robust, modern)
- Fallback: OSQP → SCS
- Regularization for ill-conditioned matrices (ridge if min eigenvalue < 1e-8)

### 4. Constraint System

Protocol-based constraints with apply() and validate() methods:

```python
class Constraint(Protocol):
    def apply(self, w: cp.Variable, context: dict) -> list[cp.Constraint]: ...
    def validate(self, context: dict) -> list[str]: ...
```

Implemented constraints:
- **BudgetConstraint:** sum(w) = target
- **GrossLeverageConstraint:** sum(|w|) ≤ max_leverage
- **BoxConstraint:** w_min ≤ w_i ≤ w_max
- **SectorConstraint:** sum(w_i for i in sector) ≤ max_sector_weight
- **FactorExposureConstraint:** |w'B_k - target| ≤ tolerance
- **TurnoverConstraint:** sum(|w - w₀|) ≤ max_turnover
- **ReturnTargetConstraint:** μ'w ≥ target_return

### 5. Stress Testing

Two scenario types with position-level attribution:

**Historical Scenarios:**
- GFC 2008 (Sep-Nov 2008)
- COVID 2020 (Feb-Mar 2020)
- Rate Hike 2022 (Jan-Jun 2022)

**Hypothetical Scenarios:**
- User-defined factor shocks
- Pre-defined RATE_SHOCK scenario

**P&L Computation:**
```python
P&L = sum_k(exposure_k × factor_shock_k) + optional_specific_estimate

Factor contribution_k = exposure_k × shock_k
```

Optional specific risk estimate uses 2-sigma conservative tail.

### 6. Coverage and Data Quality

- **Minimum coverage:** 80% of portfolio weights must have risk data
- **Validation:** Covariance PSD check, factor alignment verification
- **Provenance:** All results include dataset_version_ids for reproducibility

## Consequences

### Positive
1. **Full integration** with our factor infrastructure (no impedance mismatch)
2. **Extensible** constraint system for custom requirements
3. **Industry-standard** Barra methodology for risk decomposition
4. **Auditable** with dataset versioning and provenance tracking

### Negative
1. **Linear factor model** assumes linear risk relationships
2. **Parametric VaR** assumes normal distribution (underestimates tail risk)
3. **Computational cost** of N×N covariance for large universes

### Mitigations
1. **Stress testing** validates model under extreme (non-linear) conditions
2. **CVaR** provides better tail risk estimate than VaR
3. **Factor model** reduces dimensionality (K factors << N assets)

## Implementation Details

### File Structure
```
libs/risk/
├── __init__.py                  # Module exports
├── factor_covariance.py         # T2.2: Factor covariance estimation
├── specific_risk.py             # T2.2: Specific risk estimation
├── barra_model.py               # T2.3: Risk model assembly
├── risk_decomposition.py        # T2.3: MCTR/CCTR decomposition
├── portfolio_optimizer.py       # T2.4: cvxpy optimization
└── stress_testing.py            # T2.4: Scenario stress tests
```

### Key Classes

| Class | Purpose |
|-------|---------|
| `BarraRiskModel` | Risk model with covariance, loadings, specific risks |
| `PortfolioOptimizer` | cvxpy-based portfolio optimization |
| `StressTester` | Historical and hypothetical stress testing |
| `RiskDecomposer` | Portfolio risk decomposition (MCTR/CCTR) |

### Performance Requirements

| Operation | Target | Actual (M1 Mac) |
|-----------|--------|-----------------|
| Optimization (100 stocks) | < 5s | ~0.5s |
| Optimization (500 stocks) | < 10s | ~2s |
| Stress test (single scenario) | < 1s | ~0.05s |

### Storage Schema

Results can be stored in Parquet format:
- `data/analytics/optimizer_solutions.parquet`
- `data/analytics/optimal_weights.parquet`
- `data/analytics/stress_test_results.parquet`

## Related Documentation

- [P4T2_TASK.md](../ARCHIVE/TASKS_HISTORY/P4T2_DONE.md): Task specification
- [risk-models.md](../CONCEPTS/risk-models.md): Risk model theory
- [covariance-estimation.md](../CONCEPTS/covariance-estimation.md): Covariance methodology
