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
        correlation_threshold: float = 0.70,  # Alert if corr > 70%
        allow_short_positions: bool = False,  # Enable market-neutral support
    ):
        """
        Args:
            method: Allocation method (see below)
            per_strategy_max: Maximum total contribution from any strategy (pre-normalization cap)
            correlation_threshold: Alert if inter-strategy correlation exceeds this
            allow_short_positions: When True, negative weights (shorts) are preserved
                                   and normalization uses GROSS exposure for market-neutral portfolios.
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

Notes:
- `allow_short_positions` is an explicit initialization parameter (default False). When enabled the allocator preserves negative weights (supporting market-neutral strategies), uses gross exposure for normalization in zero-net portfolios, and enables tests that validate market-neutral behavior.
- `check_correlation()` alerts on **positive** pairwise Pearson correlation above `correlation_threshold`. Negative correlations (diversifying) do not trigger warnings.

### Allocation Methods

#### 1. Rank Aggregation (Default, Most Robust)

**Methodology:**
1. Rank symbols within each strategy by score (higher score = better rank)
2. Normalize ranks to [0, 1] range (reciprocal rank weighting)
3. Average ranks across strategies (each symbol gets mean of its ranks)
4. Convert average ranks to weights (normalize to sum to 1.0)

**Advantages:**
- Robust to outlier scores
- Handles different signal scales naturally
- Equal influence from each strategy (democratic)
- Standard approach in rank-based portfolio construction

**Disadvantages:**
- Loses information about signal strength magnitude
- Treats all strategies equally (ignores quality differences)

**Best for:**
- Diverse strategies with different scoring methods
- When strategies have different score distributions
- Default choice for multi-alpha portfolios

#### 2. Inverse Volatility Weighting (Risk-Aware)

**Methodology:**
1. Extract volatility for each strategy from `strategy_stats`
2. Calculate inverse volatility weights: `weight_i = (1/vol_i) / Σ(1/vol_j)`
3. Apply strategy weights to symbol allocations
4. Aggregate across symbols and normalize

**Advantages:**
- Risk-aware: reduces allocation to volatile strategies
- Improves risk-adjusted returns (Sharpe ratio)
- Leverages historical volatility as risk proxy

**Disadvantages:**
- Requires accurate volatility estimates
- Backward-looking (past vol may not predict future)
- Penalizes high-conviction volatile strategies

**Best for:**
- Strategies with stable long-term volatility profiles
- Risk-focused allocation (maximize Sharpe)
- When historical volatility is predictive

#### 3. Equal Weight (Baseline)

**Methodology:**
1. Normalize weights within each strategy (sum to 1.0)
2. Average weights across strategies (equal influence)
3. Normalize final weights to sum to 1.0

**Advantages:**
- Simple, no estimation risk
- Equal influence from each strategy

**Disadvantages:**
- Ignores strategy quality (Sharpe, volatility)
- Ignores signal strength within strategy
- Usually sub-optimal vs rank aggregation or inverse vol

**Best for:**
- Baseline comparison
- Strategies with similar Sharpe ratios
- When no better information available

### Safety Constraints

#### Per-Strategy Concentration Limits

**Implementation:** `per_strategy_max` parameter (default 0.40 = 40%)

**Enforcement:**
1. Calculate total contribution from each strategy across ALL symbols (GROSS exposure)
2. If total exceeds `per_strategy_max`, scale down proportionally
3. Example: Strategy contributes 0.60 total → scale by 0.40/0.60 = 0.6667

**Critical design note:**
- Cap is enforced on the **total contribution** (not per-symbol)
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
- Pairwise Pearson correlation **>** `correlation_threshold` (default 0.70)
- **Only positive correlations** above the threshold trigger warnings (positive correlation indicates redundancy). Negative correlations (diversification) do not trigger alerts.
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

... (unchanged)

## Implementation Notes

... (unchanged, listing files)

