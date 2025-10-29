---
id: P2T1
title: "Multi-Alpha Allocator"
phase: P2
task: T1
priority: HIGH
owner: "@development-team"
state: TASK
created: 2025-10-29
dependencies: ["P1T6"]
estimated_effort: "5-7 days"
related_adrs: []
related_docs: []
features: []
---

# P2T1: Multi-Alpha Allocator

**Phase:** P2 (Advanced Features, 91-120 days)
**Status:** TASK (Not Started)
**Priority:** HIGH
**Owner:** @development-team
**Created:** 2025-10-29
**Estimated Effort:** 5-7 days

---

## Naming Convention

**This task:** `P2T1_TASK.md` → `P2T1_PROGRESS.md` → `P2T1_DONE.md`

**Where:**
- **P2** = Phase 2 (Advanced Features/91-120 days)
- **T1** = Task 1 within P2
- No sub-features needed (self-contained task)

---

## Objective

Implement a multi-alpha capital allocation system that blends signals from multiple trading strategies with risk-aware weighting and correlation monitoring.

**Success looks like:**
- Multiple strategies can contribute to portfolio construction simultaneously
- Capital allocation is risk-aware (not just signal strength)
- System detects and alerts on excessive strategy correlation
- Per-strategy maximum allocation enforced (prevents over-concentration)
- Allocation methods are configurable (rank aggregation, inverse volatility, equal weight)

---

## Acceptance Criteria

**Functional Requirements:**
- [ ] **AC1:** Rank aggregation method implemented correctly (averages normalized ranks across strategies)
- [ ] **AC2:** Inverse volatility weighting implemented (weights inversely proportional to recent realized volatility)
- [ ] **AC3:** Equal weight baseline implemented (simple average across strategies)
- [ ] **AC4:** Per-strategy maximum enforced (default 40%, prevents over-concentration in single strategy)
- [ ] **AC5:** Correlation monitoring alerts when inter-strategy correlation >70% (logged as WARNING + emitted to Redis pub/sub 'alerts' channel with metric name='strategy_correlation')
- [ ] **AC6:** Total allocated weight sums to 100% (or configured total, within 0.01% tolerance)

**Integration Requirements:**
- [ ] **AC7:** Orchestrator integration: Signal objects → DataFrame conversion → allocator → aggregated Signal
- [ ] **AC8:** Supports 3+ strategies concurrently (baseline, momentum, mean reversion minimum)

**Non-Functional Requirements:**
- [ ] **AC9:** Performance: Allocation <500ms for 10 strategies, 100 symbols (measured via pytest-benchmark in integration tests)
- [ ] **AC10:** All tests pass with >90% coverage
- [ ] **AC11:** ADR documenting allocation methodology created

---

## Approach

### High-Level Plan

1. **Create MultiAlphaAllocator class** with pluggable allocation methods (rank aggregation, inverse volatility, equal weight)
2. **Implement rank aggregation** method: normalize strategy ranks, average across strategies
3. **Implement inverse volatility weighting**: calculate recent realized volatility per strategy, weight inversely
4. **Implement equal weight baseline**: simple average of normalized weights
5. **Add correlation monitoring**: check inter-strategy correlation, alert if >threshold
6. **Add per-strategy caps**: enforce maximum allocation percentage per strategy
7. **Create orchestrator integration helpers**: Signal→DataFrame conversion and reverse
8. **Add comprehensive tests**: unit tests for each method, integration tests with realistic scenarios
9. **Create ADR**: document allocation methodology, parameter tuning, trade-offs
10. **Add documentation**: usage examples, orchestrator integration pattern, parameter guidelines

### Logical Components

Break this task into components, each following the 4-step pattern:

**Component 1: Core Allocator Class (rank aggregation + equal weight)**
- Implement MultiAlphaAllocator class with rank aggregation and equal weight methods
- Create test cases for allocation logic, edge cases (empty signals, single strategy, tie-breaking)
- Request zen-mcp review (clink + codex)
- Run `make ci-local`
- Commit after approval + CI pass

**Component 2: Inverse Volatility Weighting**
- Implement inverse volatility weighting method with lookback window configuration
- Create test cases for volatility calculation, weighting logic, edge cases (zero volatility, missing data)
- Request zen-mcp review (clink + codex)
- Run `make ci-local`
- Commit after approval + CI pass

**Component 3: Correlation Monitoring + Per-Strategy Caps**
- Implement correlation calculation and alerting (>70% threshold)
- Implement per-strategy maximum allocation enforcement (default 40%)
- Create test cases for correlation detection, cap enforcement, alert emission
- Request zen-mcp review (clink + codex)
- Run `make ci-local`
- Commit after approval + CI pass

**Component 4: Orchestrator Integration**
- Add Signal→DataFrame conversion helpers
- Integrate allocator into orchestrator workflow
- Create integration tests with realistic multi-strategy scenarios
- Request zen-mcp review (clink + codex)
- Run `make ci-local`
- Commit after approval + CI pass

**Component 5: Documentation + ADR**
- Create ADR documenting allocation methodology and design decisions
- Add usage documentation with examples
- Update orchestrator documentation with allocation pattern
- Request zen-mcp review (clink + codex)
- Commit after approval

---

## Technical Details

### Files to Create
- `libs/allocation/multi_alpha.py` - Core allocator implementation
- `libs/allocation/__init__.py` - Package initialization
- `tests/libs/allocation/test_multi_alpha.py` - Comprehensive unit tests
- `tests/libs/allocation/test_integration.py` - Integration tests with orchestrator
- `docs/ADRs/ADR-XXX-multi-alpha-allocation.md` - Architecture decision record
- `docs/CONCEPTS/multi-alpha-allocation.md` - Allocation methodology explanation

### Files to Modify
- `apps/orchestrator/orchestrator.py` - Integrate allocator into signal aggregation flow
- `libs/common/signal.py` - Add Signal→DataFrame conversion helpers (if needed)

### APIs/Contracts

**MultiAlphaAllocator Interface:**
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
        ...

    def allocate(
        self,
        signals: dict[str, pl.DataFrame],  # strategy_id -> signals (symbol, score, weight)
        strategy_stats: dict[str, dict]     # strategy_id -> {vol, sharpe, ...}
    ) -> pl.DataFrame:
        """
        Allocate capital weights across strategies.

        Args:
            signals: Dictionary mapping strategy_id to DataFrames with columns [symbol, score, weight]
            strategy_stats: Dictionary mapping strategy_id to statistics dicts with keys {vol, sharpe, ...}.
                           Required for 'inverse_vol' method. Can be empty dict or None for 'rank_aggregation'
                           and 'equal_weight' methods.

        Returns:
            pl.DataFrame with columns [symbol, final_weight, contributing_strategies]
        """
        ...

    def check_correlation(
        self,
        recent_returns: dict[str, pl.DataFrame]
    ) -> dict[str, float]:
        """
        Returns: {(strategy1, strategy2): correlation_coefficient}
        Emits alert if any pair > correlation_threshold
        """
        ...
```

**Orchestrator Integration Pattern:**
```python
# apps/orchestrator/orchestrator.py

# 1. Collect Signal objects from each strategy
signals = {
    'alpha_baseline': signal_service.get_signal(),
    'momentum': momentum_strategy.get_signal(),
    'mean_reversion': mean_reversion_strategy.get_signal()
}

# 2. Convert Signal.target_weights (dict) to pl.DataFrame per strategy
signal_dfs = {
    strategy_id: _signal_to_dataframe(signal)
    for strategy_id, signal in signals.items()
}

# 3. Get recent strategy statistics for inverse-vol weighting
strategy_stats = {
    strategy_id: _get_strategy_stats(strategy_id)
    for strategy_id in signals.keys()
}

# 4. Allocate across strategies
allocator = MultiAlphaAllocator(method='rank_aggregation')
blended_df = allocator.allocate(signal_dfs, strategy_stats)

# 5. Convert back to Signal object for execution
aggregated_signal = _dataframe_to_signal(blended_df)

# 6. Send to execution gateway
execution_gateway.submit(aggregated_signal)
```

### Database Changes
- None (allocation is stateless computation)
- Strategy statistics (volatility, Sharpe) can be retrieved from existing metrics in Redis or computed on-the-fly

### Data Model

**Input:**
```python
# signals: dict[str, pl.DataFrame]
# Each DataFrame has columns: [symbol, score, weight]

# Example:
signals = {
    'alpha_baseline': pl.DataFrame({
        'symbol': ['AAPL', 'MSFT', 'GOOGL'],
        'score': [0.8, 0.6, 0.7],
        'weight': [0.35, 0.30, 0.35]
    }),
    'momentum': pl.DataFrame({
        'symbol': ['AAPL', 'TSLA', 'NVDA'],
        'score': [0.7, 0.9, 0.8],
        'weight': [0.33, 0.34, 0.33]
    })
}

# strategy_stats: dict[str, dict]
strategy_stats = {
    'alpha_baseline': {'vol': 0.15, 'sharpe': 1.2},
    'momentum': {'vol': 0.25, 'sharpe': 0.8}
}
```

**Output:**
```python
# pl.DataFrame with columns: [symbol, final_weight, contributing_strategies]

blended_df = pl.DataFrame({
    'symbol': ['AAPL', 'MSFT', 'GOOGL', 'TSLA', 'NVDA'],
    'final_weight': [0.25, 0.15, 0.20, 0.20, 0.20],
    'contributing_strategies': [
        ['alpha_baseline', 'momentum'],
        ['alpha_baseline'],
        ['alpha_baseline'],
        ['momentum'],
        ['momentum']
    ]
})
```

---

## Dependencies

**Blockers (must complete before starting):**
- P1T6: Advanced Strategies - Provides multiple strategies (momentum, mean reversion, ensemble) to allocate across

**Nice-to-have (can start without):**
- P1T7: Risk Management System - Provides position limits that may be used as allocation constraints (optional integration)

**Blocks (other tasks waiting on this):**
- P2T5: Live Rollout Preparation - Multi-alpha allocation is a key feature for live trading (demonstrates diversification)

---

## Risks & Mitigations

| Risk | Impact | Probability | Mitigation |
|------|--------|-------------|------------|
| High correlation between strategies reduces diversification benefit | Medium | Medium | Implement correlation monitoring with alerts at >70% threshold. Consider disabling or reducing weight of highly correlated strategies. |
| Inverse volatility weighting penalizes strategies during temporary vol spikes | Medium | Low | Use longer lookback window (30+ days) to smooth volatility estimates. Allow manual override via config. |
| Rank aggregation may over-allocate to stocks with consistently mediocre rankings | Low | Low | Implement minimum score threshold (e.g., only include stocks with score >0.5 from at least one strategy). |
| Performance degrades with many strategies/symbols | Medium | Low | Implement efficient polars operations. Benchmark with realistic scenarios (10 strategies, 100 symbols). Target <500ms allocation time. |
| Orchestrator integration breaks existing single-strategy flow | Medium | Low | Maintain backward compatibility: if only one strategy, bypass allocator and use signal directly. Add feature flag for gradual rollout. |

---

## Testing Strategy

### Test Coverage Needed

**Unit tests (`test_multi_alpha.py`):**
- Rank aggregation correctness (3+ strategies, tie-breaking, normalization)
- Inverse volatility weighting (zero volatility handling, missing data, lookback window)
- Equal weight baseline (simple averaging, normalization)
- Per-strategy cap enforcement (40% max, edge cases with <3 strategies)
- Correlation monitoring (threshold detection, alert emission, edge cases)
- Weight normalization (total sums to 100%, tolerance check)
- Edge cases: empty signals, single strategy, no overlap between strategies, all strategies recommend same symbols

**Integration tests (`test_integration.py`):**
- Full orchestrator flow with 3+ strategies
- Signal→DataFrame→allocate→Signal round-trip
- Performance benchmark (10 strategies, 100 symbols, <500ms)
- Redis alert emission verification (correlation alerts)
- Strategy stats integration (volatility calculation from recent returns)

**Manual Testing:**
- [ ] Allocate across alpha_baseline + momentum + mean_reversion
- [ ] Verify final weights sum to 100% (within 0.01%)
- [ ] Trigger correlation alert by feeding highly correlated signals
- [ ] Verify per-strategy cap enforcement (force one strategy to dominate, check capping)
- [ ] Performance test with realistic data (50+ symbols, 5+ strategies)

---

## Documentation Requirements

### Must Create/Update
- [x] ADR documenting allocation methodology (rank aggregation, inverse-vol, equal-weight trade-offs)
- [x] Concept doc in `/docs/CONCEPTS/multi-alpha-allocation.md` explaining allocation theory
- [ ] Update `/docs/GETTING_STARTED/PROJECT_STATUS.md` when complete (mark P2T1 as done)

### Optional (if time permits)
- [ ] Jupyter notebook with allocation examples and visualizations
- [ ] Parameter tuning guide (per-strategy max, correlation threshold, lookback window)

---

## Related

**ADRs:**
- [ADR-XXX: Multi-Alpha Allocation Methodology](../ADRs/ADR-XXX-multi-alpha-allocation.md) - To be created

**Documentation:**
- [Advanced Strategies](../GETTING_STARTED/REPO_MAP.md#strategies) - P1T6 implementation
- [Risk Management](../CONCEPTS/risk-management.md) - Position limits and circuit breakers

**Tasks:**
- Depends on: [P1T6: Advanced Strategies](./P1T6_DONE.md)
- Related to: [P2T0: TWAP Slicer](./P2T0_TASK.md) (can run in parallel)
- Blocks: [P2T5: Live Rollout Preparation](./P2T5_TASK.md)

---

## Notes

**Allocation Methods Comparison:**

1. **Rank Aggregation:**
   - Pros: Robust to outlier scores, handles different signal scales naturally
   - Cons: Loses information about signal strength magnitude
   - Best for: Diverse strategies with different scoring methods

2. **Inverse Volatility:**
   - Pros: Risk-aware, reduces allocation to volatile strategies
   - Cons: Penalizes strategies during temporary volatility spikes
   - Best for: Strategies with stable long-term volatility profiles

3. **Equal Weight:**
   - Pros: Simple, no estimation risk
   - Cons: Ignores strategy quality and risk
   - Best for: Baseline comparison, strategies with similar Sharpe ratios

**Implementation Priority:**
- Start with rank aggregation (most robust)
- Add equal weight (simple baseline for comparison)
- Add inverse volatility last (requires volatility calculation infrastructure)

**Future Enhancements (P3 or later):**
- Mean-variance optimization (requires covariance estimation)
- Black-Litterman allocation (requires return forecasts + uncertainty)
- Machine learning meta-strategy (learns optimal weights from historical performance)
- Dynamic allocation based on market regime detection

---

## Task Creation Review Checklist

**RECOMMENDED:** Before starting work, request task creation review to validate scope and requirements.

See [`.claude/workflows/13-task-creation-review.md`](../../.claude/workflows/13-task-creation-review.md) for workflow details.

**Review validates:**
- [ ] Objective is clear and measurable
- [ ] Success criteria are testable
- [ ] Functional requirements are comprehensive
- [ ] Trading safety requirements specified (per-strategy caps, correlation monitoring for risk control)
- [ ] Non-functional requirements documented (performance <500ms)
- [ ] Component breakdown follows 4-step pattern (5 components defined)
- [ ] Time estimates are reasonable (5-7 days)
- [ ] Dependencies and blockers identified (P1T6 required)
- [ ] ADR requirement clear (YES - allocation methodology)
- [ ] Test strategy comprehensive (unit + integration + performance)

**This task qualifies for review:**
- ✅ Complex task (5-7 days, >4 hour threshold)
- ✅ Architectural change (new allocation subsystem)
- ✅ New feature development

**How to request:**
```bash
# Phase 1: Gemini validation
"Review docs/TASKS/P2T1_TASK.md using clink + gemini planner"

# Phase 2: Codex synthesis
"Use clink + codex planner with continuation_id to synthesize readiness assessment"
```

---

## State Transition Instructions

**When starting this task:**

```bash
# 1. Rename file
git mv docs/TASKS/P2T1_TASK.md docs/TASKS/P2T1_PROGRESS.md

# 2. Update front matter in P2T1_PROGRESS.md:
#    state: PROGRESS
#    started: YYYY-MM-DD

# 3. Commit
git add docs/TASKS/P2T1_PROGRESS.md
git commit -m "Start P2T1: Multi-Alpha Allocator"
```

**Or use automation:**
```bash
./scripts/tasks.py start P2T1
```
