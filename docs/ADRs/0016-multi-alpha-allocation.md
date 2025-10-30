# ADR-0016: Multi-Alpha Capital Allocation System

- Status: Accepted
- Date: 2025-10-29
- Related Task: P2T1

## Context

### Problem Statement

As the platform evolves from a single-strategy system (alpha_baseline) to supporting multiple trading strategies (momentum, mean reversion, ensemble), we need a systematic way to allocate capital across competing strategies while managing risk and avoiding over-concentration.

**Current state:**
- Single strategy (alpha_baseline) produces signals directly consumed by orchestrator
- No mechanism to blend signals from multiple strategies
- No risk-aware allocation (volatility, correlation)
- No per-strategy concentration limits

**Requirements:**
- Support 3+ concurrent strategies (baseline, momentum, mean reversion minimum)
- Risk-aware allocation considering strategy volatility and correlation
- Per-strategy concentration limits (prevent over-reliance on single strategy)
- Correlation monitoring with alerts (detect redundant strategies)
- Configurable allocation methods for different market conditions
- Backward compatibility with single-strategy mode
- Performance: <500ms for 10 strategies × 100 symbols

**Why now:**
- P1T6 (Advanced Strategies) delivers multiple strategy implementations
- Live rollout (P2T5) requires diversification across strategies for risk management
- Single-strategy limitation blocks multi-alpha research and backtesting

### Trading Context

In quantitative portfolio management, combining multiple alpha signals (strategies) is a fundamental technique to:
1. **Diversify risk:** Reduce dependence on single strategy's performance
2. **Improve Sharpe ratio:** Uncorrelated strategies can increase risk-adjusted returns
3. **Reduce drawdowns:** Strategy correlation <1.0 provides smoother equity curves
4. **Exploit multiple edges:** Different strategies capture different market inefficiencies

**Key challenges:**
- **Over-concentration:** Allocating too much to single strategy increases risk
- **High correlation:** Redundant strategies provide little diversification benefit
- **Volatility mismatch:** Volatile strategies can dominate equal-weight portfolios
- **Signal scale differences:** Strategies may use different score ranges/meanings

## Decision

Implement **MultiAlphaAllocator** with three pluggable allocation methods and safety constraints.

### Core Design

**Class:** `libs/allocation/multi_alpha.py::MultiAlphaAllocator`

**Interface:**
```python
from typing import Literal
import polars as pl

AllocMethod = Literal['rank_aggregation', 'inverse_vol', 'equal_weight']

class MultiAlphaAllocator:
    def __init__(
        self,
        method: AllocMethod = 'rank_aggregation',
        per_strategy_max: float = 0.40,  # Max 40% to any strategy
        correlation_threshold: float = 0.70  # Alert if corr > 70%
    ):
        """
        Args:
            method: Allocation method (see below)
            per_strategy_max: Maximum total contribution from any strategy (pre-normalization cap)
            correlation_threshold: Alert if inter-strategy correlation exceeds this
        """

    def allocate(
        self,
        signals: dict[str, pl.DataFrame],  # strategy_id -> [symbol, score, weight]
        strategy_stats: dict[str, dict]     # strategy_id -> {vol, sharpe, ...}
    ) -> pl.DataFrame:
        """Returns: [symbol, final_weight, contributing_strategies]"""

    def check_correlation(
        self,
        recent_returns: dict[str, pl.DataFrame]  # strategy_id -> [date, return]
    ) -> dict[tuple[str, str], float]:
        """Returns: {(strat1, strat2): correlation}, emits alerts if > threshold"""
```

### Allocation Methods

#### 1. Rank Aggregation (Default, Most Robust)

**Methodology:**
1. Rank symbols within each strategy by score (descending)
2. Convert ranks to weights using reciprocal rank: `weight = 1 / rank`
3. Normalize weights within each strategy (sum to 1.0)
4. Apply per-strategy caps
5. Aggregate across strategies
6. Final normalization to 100%

**Advantages:**
- Robust to outlier scores
- Handles different signal scales naturally (score=0.8 vs score=50)
- Equal influence from each strategy (democratic)
- Standard approach in rank-based portfolio construction

**Disadvantages:**
- Loses information about signal strength magnitude
- rank=1 (score=0.9) treated same as rank=1 (score=0.5)
- Treats all strategies equally (ignores quality differences)

**Best for:**
- Diverse strategies with different scoring methods
- When strategies have different score distributions
- Default choice for multi-alpha portfolios

#### 2. Inverse Volatility Weighting (Risk-Aware)

**Methodology:**
1. Extract recent realized volatility for each strategy from `strategy_stats`
2. Calculate inverse volatility weights: `weight_i = (1/vol_i) / Σ(1/vol_j)`
3. Scale each strategy's symbol weights by strategy weight
4. Apply per-strategy caps
5. Aggregate and normalize

**Advantages:**
- Risk-aware: reduces allocation to volatile strategies
- Improves risk-adjusted returns (Sharpe ratio)
- Leverages historical volatility as risk proxy
- Standard in risk parity portfolios

**Disadvantages:**
- Requires accurate volatility estimates (30+ day lookback)
- Backward-looking (past vol may not predict future)
- Penalizes high-conviction volatile strategies
- Requires `strategy_stats` with 'vol' key (validation enforced)

**Best for:**
- Strategies with stable long-term volatility profiles
- Risk-focused allocation (maximize Sharpe)
- When historical volatility is predictive

#### 3. Equal Weight (Baseline)

**Methodology:**
1. Normalize weights within each strategy (sum to 1.0)
2. Average symbol weights across strategies
3. Apply per-strategy caps
4. Final normalization

**Advantages:**
- Simple, no estimation risk
- No parameters to tune
- Good baseline for comparison
- Equal influence from each strategy

**Disadvantages:**
- Ignores strategy quality (Sharpe, volatility)
- Ignores signal strength within strategy
- May over-allocate to poor strategies

**Best for:**
- Baseline comparison
- Strategies with similar Sharpe ratios
- When no better information available

### Safety Constraints

#### Per-Strategy Concentration Limits

**Implementation:** `per_strategy_max` parameter (default 0.40 = 40%)

**Enforcement:**
1. Calculate total contribution from each strategy across ALL symbols
2. If total exceeds `per_strategy_max`, scale down proportionally
3. Example: Strategy contributes 0.60 total → scale by 0.40/0.60 = 0.6667

**Critical design note:**
- Cap is enforced on **total contribution** (not per-symbol)
- Prevents strategy from exceeding limit by spreading across many symbols
- Applied **before final normalization** (relative limit, not absolute)
- After normalization, final weights may differ from pre-norm caps

**Rationale:**
- Prevents over-concentration in single strategy (diversification)
- Reduces impact if one strategy degrades
- Standard risk management practice (no single strategy >40% typical)

#### Correlation Monitoring

**Implementation:** `check_correlation()` method

**Alerts triggered when:**
- Pairwise Pearson correlation's absolute value > `correlation_threshold` (default 0.70)
- **Current implementation:** Uses `abs(correlation) > threshold`, so both high positive correlation (+0.8, redundant strategies) and high negative correlation (-0.8, diversifying strategies) trigger alerts
- **Known limitation:** Negative correlation indicates diversification (good), not redundancy. Future enhancement should only alert on positive correlation > threshold.
- Logged as WARNING with strategy pair + correlation value
- **Monitoring:** Currently log-only (no Prometheus metrics). Future enhancement: emit metric to enable dashboard/alerting.

**Rationale:**
- High correlation (>0.70) indicates redundant strategies
- Little diversification benefit from highly correlated strategies
- Early warning for strategy review (consider disabling/reducing one)

### Orchestrator Integration

**Pattern:**
```python
# apps/orchestrator/orchestrator.py

# 1. Convert Signal objects → Polars DataFrames
def signals_to_dataframe(signals: list[Signal]) -> pl.DataFrame:
    """Returns: [symbol, score, weight]"""

# 2. Run multi-strategy allocation
signal_dfs = {sid: signals_to_dataframe(sigs) for sid, sigs in signals.items()}
allocator = MultiAlphaAllocator(method='rank_aggregation')
blended_df = allocator.allocate(signal_dfs, strategy_stats={})

# 3. Convert blended DataFrame → Signal objects
def dataframe_to_signals(df: pl.DataFrame) -> list[Signal]:
    """Returns: list[Signal] with final_weight as target_weight"""
```

**Backward compatibility:**
- Single-strategy mode (str) bypasses allocator
- Multi-strategy mode (list[str]) uses allocator
- `run()` method accepts `strategy_id: str | list[str]`

## Consequences

### Positive

**Risk management:**
- ✅ Per-strategy caps prevent over-concentration (default 40% max)
- ✅ Correlation monitoring detects redundant strategies
- ✅ Inverse volatility weighting reduces allocation to risky strategies
- ✅ Diversification across strategies reduces drawdowns

**Flexibility:**
- ✅ Three allocation methods for different market conditions
- ✅ Configurable parameters (method, caps, correlation threshold)
- ✅ Backward compatible with single-strategy mode
- ✅ Strategy-agnostic (works with any strategy producing signals)

**Performance:**
- ✅ Polars DataFrames provide efficient computation
- ✅ <500ms allocation for 10 strategies × 100 symbols (benchmarked)
- ✅ Stateless computation (no database overhead)

**Testing:**
- ✅ 92% test coverage for multi_alpha.py (58 unit tests)
- ✅ 14 integration tests with realistic scenarios
- ✅ Edge case handling (empty signals, single strategy, zero volatility)

### Negative

**Complexity:**
- ⚠️ Adds new subsystem to maintain (MultiAlphaAllocator)
- ⚠️ Three allocation methods increase configuration surface
- ⚠️ Requires understanding of allocation theory for parameter tuning
- ⚠️ Conversion overhead (Signal → DataFrame → Signal)

**Limitations:**
- ⚠️ Rank aggregation loses signal strength information
- ⚠️ Inverse volatility backward-looking (past vol ≠ future vol)
- ⚠️ Per-strategy caps are relative (pre-normalization), not absolute
- ⚠️ Equal weight ignores strategy quality differences

**Operational:**
- ⚠️ Requires `strategy_stats` for inverse_vol method (dependency on metrics)
- ⚠️ Correlation monitoring requires historical returns (storage/compute)
- ⚠️ Parameter tuning needed (per_strategy_max, correlation_threshold)

**MVP Limitations:**
- ⚠️ Multi-strategy execution requires P1T6 (Advanced Strategies) completion
- ⚠️ SignalServiceClient doesn't support strategy_id parameter yet (infrastructure in place)
- ⚠️ Infrastructure committed, full execution deferred to P1T6 integration

### Risks

| Risk | Impact | Mitigation |
|------|--------|------------|
| High correlation reduces diversification benefit | Medium | Correlation monitoring with >70% alerts; consider disabling correlated strategies |
| Inverse volatility penalizes strategies during temporary spikes | Medium | Use 30+ day lookback to smooth estimates; allow manual override |
| Rank aggregation over-allocates to mediocre rankings | Low | Implement minimum score threshold (future enhancement) |
| Performance degrades with many strategies/symbols | Medium | Polars efficient operations; benchmarked at <500ms for realistic loads |
| Parameter tuning (caps, thresholds) requires expertise | Low | Sensible defaults (40% cap, 70% correlation); document tuning guide |

## Alternatives Considered

### 1. Equal Weight Only (Rejected)

**Approach:** Simple average across strategies, no other methods

**Pros:**
- Simplest implementation
- No parameters to tune
- No estimation risk

**Cons:**
- Ignores strategy quality and risk
- No risk-aware allocation
- Sub-optimal risk-adjusted returns

**Why not:** Too simplistic; modern portfolio management requires risk-aware allocation

### 2. Mean-Variance Optimization (Deferred to P3)

**Approach:** Solve for weights maximizing Sharpe ratio given covariance matrix

**Pros:**
- Theoretically optimal (Markowitz framework)
- Maximizes risk-adjusted returns
- Well-studied in finance literature

**Cons:**
- Requires covariance estimation (unstable with limited data)
- Sensitive to estimation errors ("error maximization")
- Computationally expensive (quadratic programming)
- Over-fits to historical data

**Why not:** Too complex for MVP; estimation errors often make simpler methods better in practice. Deferred to P3 if simple methods prove insufficient.

### 3. Black-Litterman Allocation (Deferred to P3)

**Approach:** Bayesian framework combining market equilibrium with views

**Pros:**
- Incorporates uncertainty in forecasts
- Less sensitive to estimation errors than mean-variance
- Standard in institutional asset management

**Cons:**
- Requires equilibrium model (market benchmark)
- Complex implementation
- Requires return forecast uncertainties
- Significant parameter tuning

**Why not:** Overkill for MVP; simpler methods provide 80% of benefit with 20% of complexity.

### 4. Machine Learning Meta-Strategy (Future Research)

**Approach:** Train model to learn optimal weights from historical performance

**Pros:**
- Adapts to changing strategy performance
- Can capture non-linear relationships
- Automatic parameter tuning

**Cons:**
- Requires extensive historical data
- Prone to overfitting
- Hard to explain/debug
- Adds ML complexity

**Why not:** Interesting research direction but too experimental for production MVP. Consider for P3+ if simpler methods hit limits.

### 5. Fixed Strategy Weights (Rejected)

**Approach:** Manually configure weights (e.g., 50% baseline, 30% momentum, 20% mean reversion)

**Pros:**
- Simple configuration
- Full control
- Transparent

**Cons:**
- Requires manual tuning
- Doesn't adapt to strategy performance
- No risk awareness
- Stale as strategies evolve

**Why not:** Defeats purpose of multi-alpha system; want dynamic risk-aware allocation.

## Implementation Notes

### File Structure

**Created:**
- `libs/allocation/multi_alpha.py` (785 lines) - Core allocator implementation
- `libs/allocation/__init__.py` - Package initialization
- `tests/libs/allocation/test_multi_alpha.py` (876 lines) - 58 unit tests
- `tests/libs/allocation/test_integration.py` (426 lines) - 14 integration tests

**Modified:**
- `apps/orchestrator/orchestrator.py` (+166 lines) - Multi-strategy support
  - Added `signals_to_dataframe()` helper
  - Added `dataframe_to_signals()` helper
  - Modified `__init__()` for allocation parameters
  - Modified `run()` to accept `str | list[str]` for strategy_id
  - Added `_run_multi_strategy()` workflow method

### Key Implementation Decisions

**1. Polars DataFrames over Pandas:**
- 10-100x faster than pandas for group operations
- Built-in lazy evaluation
- Better memory efficiency
- Native multi-threading

**2. Reciprocal Rank over Normalized Rank:**
```python
# Reciprocal rank (chosen)
weight = 1 / rank  # rank=1 → 1.0, rank=2 → 0.5, rank=3 → 0.33

# vs. Normalized rank (rejected)
weight = (max_rank - rank + 1) / sum(...)  # Linear decay
```
**Rationale:** Reciprocal rank is standard in information retrieval and rank-based portfolios; provides stronger preference for top-ranked symbols.

**3. Two-Phase Normalization:**
- Phase 1: Normalize within each strategy (equal influence)
- Phase 2: Apply per-strategy caps on totals
- Phase 3: Final normalization to 100%

**Rationale:** Ensures each strategy contributes proportionally while respecting caps.

**4. Strict Type Validation for inverse_vol:**
```python
if self.method == "inverse_vol":
    if strategy_stats is None:
        raise ValueError("strategy_stats required for inverse_vol method but got None")
```
**Rationale:** Fail fast with clear error message rather than silent fallback.

### Testing Strategy

**Unit tests (`test_multi_alpha.py`):**
- 58 tests across 6 test classes
- 92% coverage for multi_alpha.py
- Edge cases: empty signals, single strategy, zero volatility, NaN handling
- Tie-breaking, normalization correctness, weight sum validation

**Integration tests (`test_integration.py`):**
- 14 tests covering full orchestrator workflow
- Signal→DataFrame→Allocate→Signal round-trip
- 3-strategy scenarios with realistic overlap
- Backward compatibility (single-strategy mode)
- Per-strategy caps enforcement

**Performance benchmarks:**
- 10 strategies × 100 symbols: ~200ms (target <500ms)
- 3 strategies × 50 symbols: ~50ms
- Polars lazy evaluation enables scaling

### Migration Path

**Phase 1: Infrastructure (P2T1 - Complete)**
- ✅ MultiAlphaAllocator implemented with 3 methods
- ✅ Orchestrator integration (backward compatible)
- ✅ Comprehensive tests (92% coverage)
- ✅ ADR and documentation

**Phase 2: Full Multi-Strategy Execution (P1T6 dependency)**
- TODO: Implement multiple strategy services (momentum, mean reversion)
- TODO: Plumb strategy_id through SignalServiceClient
- TODO: Add deferred integration test for `_run_multi_strategy()`
- TODO: Enable multi-strategy mode in orchestrator config

**Phase 3: Production Rollout (P2T5)**
- Monitor correlation alerts in production
- Tune per_strategy_max based on live performance
- Collect strategy_stats for inverse_vol method
- Compare allocation methods in backtest

### Rollback Plan

If allocation causes issues:
1. Set `ALLOCATION_METHOD=single_strategy` env var (bypass allocator)
2. Fall back to alpha_baseline only (proven strategy)
3. No database changes needed (allocation is stateless)
4. Fix allocator bug
5. Re-enable multi-strategy mode after validation

### Parameter Tuning Guidelines

**per_strategy_max (default 0.40):**
- **Conservative:** 0.30 (30% max, more diversification)
- **Balanced:** 0.40 (40% max, standard in quant funds)
- **Aggressive:** 0.50 (50% max, less diversification)
- **Rule:** Lower if strategies highly correlated; raise if low correlation

**correlation_threshold (default 0.70):**
- **Strict:** 0.60 (alert if correlation >60%)
- **Balanced:** 0.70 (standard threshold in finance)
- **Relaxed:** 0.80 (only alert on very high correlation)
- **Rule:** Lower threshold catches more redundant strategies earlier

**allocation_method:**
- **Default:** `rank_aggregation` (most robust)
- **Risk-focused:** `inverse_vol` (maximize Sharpe)
- **Baseline:** `equal_weight` (simplest, for comparison)
- **Rule:** Start with rank_aggregation; switch to inverse_vol if volatility predictive

## Related ADRs

- **ADR-0003:** Baseline Strategy with Qlib and MLFlow - First strategy implementation
- **ADR-0006:** Orchestrator Service - Integration point for allocator
- **Depends on P1T6:** Advanced Strategies (momentum, mean reversion) - Provides multiple strategies to allocate across

## Future Enhancements (P3+)

1. **Dynamic allocation based on market regime:**
   - Detect bull/bear/sideways markets
   - Adjust allocation weights based on regime
   - Example: More weight to momentum in bull markets

2. **Mean-variance optimization:**
   - Implement Markowitz portfolio optimization
   - Use shrinkage estimators for covariance (Ledoit-Wolf)
   - Compare to simpler methods in backtest

3. **Black-Litterman allocation:**
   - Bayesian framework for incorporating views
   - Combine market equilibrium with strategy forecasts
   - Better handling of forecast uncertainty

4. **Machine learning meta-strategy:**
   - Train model to learn optimal weights
   - Features: strategy returns, correlations, market conditions
   - Ensemble of allocation methods

5. **Adaptive per-strategy caps:**
   - Adjust caps based on strategy recent performance
   - Tighter caps for underperforming strategies
   - Dynamic diversification requirements

6. **Strategy health monitoring:**
   - Automatic strategy disabling if Sharpe < threshold
   - Correlation-based strategy clustering
   - Alert on strategy degradation
