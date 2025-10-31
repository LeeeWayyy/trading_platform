# Multi-Alpha Capital Allocation

## Plain English Explanation

Multi-alpha allocation is the process of combining signals from multiple trading strategies into a single portfolio. Instead of running one strategy with 100% of your capital, you split capital across multiple strategies to reduce risk and improve returns.

**The problem it solves:**

You have three trading strategies:
```python
# Strategy 1: Alpha Baseline (momentum + fundamental factors)
alpha_baseline.generate_signal()  # Says: Buy AAPL, MSFT

# Strategy 2: Momentum (price trends)
momentum.generate_signal()        # Says: Buy AAPL, GOOGL

# Strategy 3: Mean Reversion (buy dips)
mean_reversion.generate_signal()  # Says: Buy TSLA, AAPL
```

**Question:** How do you combine these into ONE portfolio?

**Naive approach (don't do this):**
```python
# Just average the weights? 🤔
portfolio = (alpha_baseline + momentum + mean_reversion) / 3
```

**Problems with naive averaging:**
1. **Ignores strategy quality:** Low-performing strategy gets same weight as high-performing
2. **Ignores strategy risk:** Volatile strategy dominates portfolio
3. **No correlation awareness:** Redundant strategies (both bet on same stocks) don't diversify
4. **No concentration limits:** One strategy could dominate 80% of portfolio

**Multi-alpha allocation solution:**
```python
from libs.allocation import MultiAlphaAllocator

# Configure allocator with safety constraints
allocator = MultiAlphaAllocator(
    method='rank_aggregation',     # How to blend signals
    per_strategy_max=0.40,          # No strategy gets >40% of capital
    correlation_threshold=0.70      # Alert if strategies >70% correlated
)

# Allocate across strategies
blended_portfolio = allocator.allocate(
    signals={'alpha_baseline': ..., 'momentum': ..., 'mean_reversion': ...},
    strategy_stats={'alpha_baseline': {'vol': 0.15}, ...}  # Optional risk stats
)
```

Now capital is allocated **risk-aware** and **diversified**, with safety limits preventing over-concentration.

## Why It Matters

### Real-World Impact

**Example 1: Single Strategy Risk**

**Scenario:** You run only alpha_baseline strategy with $100K capital

```
Jan: +8% ($108K)
Feb: +12% ($121K)
Mar: -15% ($103K)  ← Strategy stops working due to market regime change
Apr: -8% ($95K)
May: -5% ($90K)
```

**Impact:**
- Single strategy failure → -10% drawdown ($100K → $90K)
- No diversification
- High stress, temptation to shut down after losses

**Example 2: Multi-Alpha Diversification**

**Scenario:** Same $100K split across 3 strategies (rank aggregation, 40% max per strategy)

```
         Alpha    Momentum  MeanRev   Total
Jan:     +8%      +3%       +1%       +4.5%  ($104.5K)
Feb:     +12%     +5%       -2%       +6.0%  ($110.8K)
Mar:     -15%     +2%       +8%       -1.0%  ($109.7K)  ← Diversification helps!
Apr:     -8%      +1%       +6%       +0.5%  ($110.2K)
May:     -5%      +4%       +3%       +1.5%  ($111.9K)
```

**Impact:**
- Alpha_baseline fails in Mar (−15%), but other strategies offset it
- Maximum drawdown: -1% vs -10% (10x improvement!)
- Smoother equity curve, less stress
- Higher risk-adjusted returns (Sharpe ratio)

**Key insight:** Uncorrelated strategies smooth out individual strategy failures.

### Industry Statistics

**Diversification benefits (academic research):**
- 2 uncorrelated strategies: ~30% reduction in volatility vs single strategy
- 3 strategies (correlation <0.5): ~45% volatility reduction
- Diminishing returns: 5+ strategies only marginal improvement

**Institutional practice:**
- Quantitative hedge funds typically run 5-15 strategies
- Per-strategy allocation caps: 30-50% common
- Correlation monitoring: Alert if >0.6-0.7 (redundant strategies)

**Common failure modes:**
1. **Over-concentration:** 80% in one strategy → single point of failure
2. **High correlation:** All strategies bet on same stocks → no diversification
3. **Ignoring volatility:** Volatile strategy dominates equal-weight portfolio

## How It Works

### 1. Rank Aggregation Method (Default)

**Use case:** Diverse strategies with different scoring methods

**How it works:**

```python
# Step 1: Each strategy ranks its signals
alpha_baseline = [
    ('AAPL', rank=1, score=0.85),
    ('MSFT', rank=2, score=0.70),
    ('GOOGL', rank=3, score=0.65)
]

momentum = [
    ('AAPL', rank=2, score=50.2),   # Different score scale!
    ('GOOGL', rank=1, score=75.8),
    ('TSLA', rank=3, score=42.1)
]

# Step 2: Convert ranks to weights (reciprocal rank)
# rank=1 → weight=1/1=1.0, rank=2 → weight=1/2=0.5, rank=3 → weight=1/3=0.33

alpha_baseline_weights = {
    'AAPL': 1.0,
    'MSFT': 0.5,
    'GOOGL': 0.33
}

momentum_weights = {
    'AAPL': 0.5,
    'GOOGL': 1.0,
    'TSLA': 0.33
}

# Step 3: Normalize within each strategy (sum to 1.0)
alpha_baseline_weights = {
    'AAPL': 1.0/1.83 = 0.546,
    'MSFT': 0.5/1.83 = 0.273,
    'GOOGL': 0.33/1.83 = 0.180
}

momentum_weights = {
    'AAPL': 0.5/1.83 = 0.273,
    'GOOGL': 1.0/1.83 = 0.546,
    'TSLA': 0.33/1.83 = 0.180
}

# Step 4: Apply per-strategy caps (each strategy contributes ≤40%)
# Both strategies within 40% limit, no scaling needed

# Step 5: Aggregate across strategies
blended_weights = {
    'AAPL': 0.546 + 0.273 = 0.819,
    'MSFT': 0.273,
    'GOOGL': 0.180 + 0.546 = 0.726,
    'TSLA': 0.180
}

# Step 6: Final normalization (sum to 1.0)
total = 0.819 + 0.273 + 0.726 + 0.180 = 1.998

final_weights = {
    'AAPL': 0.819/1.998 = 0.410,   # 41% allocation
    'MSFT': 0.273/1.998 = 0.137,   # 13.7%
    'GOOGL': 0.726/1.998 = 0.363,  # 36.3%
    'TSLA': 0.180/1.998 = 0.090    # 9%
}
```

**Why reciprocal rank?**
- Standard in information retrieval (Google search results use this)
- Gives strong preference to top-ranked items (rank=1 gets 2x weight of rank=2)
- Handles different score scales naturally (score=0.8 vs score=50 → same rank → same weight)

**Advantages:**
- ✅ Robust to outlier scores (one strategy scores 0-1, another 0-100)
- ✅ Equal influence from each strategy (democratic)
- ✅ Standard approach in quant finance

**Disadvantages:**
- ⚠️ Loses signal strength information (rank=1 with score=0.9 same as rank=1 with score=0.51)
- ⚠️ Treats all strategies equally (ignores Sharpe ratio differences)

### 2. Inverse Volatility Method (Risk-Aware)

**Use case:** Strategies with different volatility profiles

**How it works:**

```python
# Step 1: Calculate strategy volatilities (from recent returns)
strategy_stats = {
    'alpha_baseline': {'vol': 0.15},  # 15% annualized volatility
    'momentum': {'vol': 0.25},        # 25% annualized volatility (more volatile!)
    'mean_reversion': {'vol': 0.20}   # 20% annualized volatility
}

# Step 2: Calculate inverse volatility weights
# weight_i = (1/vol_i) / sum(1/vol_j)

inv_vols = {
    'alpha_baseline': 1/0.15 = 6.67,
    'momentum': 1/0.25 = 4.00,
    'mean_reversion': 1/0.20 = 5.00
}

total_inv_vol = 6.67 + 4.00 + 5.00 = 15.67

strategy_weights = {
    'alpha_baseline': 6.67/15.67 = 0.426,    # 42.6% of capital
    'momentum': 4.00/15.67 = 0.255,          # 25.5% (reduced due to high vol!)
    'mean_reversion': 5.00/15.67 = 0.319     # 31.9%
}

# Step 3: Scale each strategy's symbol weights by strategy weight
# (then aggregate and normalize as before)
```

**Why inverse volatility?**
- More volatile strategies are riskier → allocate less capital to them
- Standard in "risk parity" portfolios (hedge funds use this)
- Improves risk-adjusted returns (Sharpe ratio)

**Example impact:**

**Equal weight (ignoring volatility):**
```
Alpha (15% vol): 33% capital → contributes 0.33 * 0.15 = 0.050 portfolio vol
Momentum (25% vol): 33% capital → contributes 0.33 * 0.25 = 0.083 portfolio vol
MeanRev (20% vol): 33% capital → contributes 0.33 * 0.20 = 0.067 portfolio vol

Portfolio vol ≈ 0.20 (20%)
Sharpe ratio: 1.0
```

**Inverse volatility:**
```
Alpha (15% vol): 43% capital → contributes 0.43 * 0.15 = 0.065 portfolio vol
Momentum (25% vol): 26% capital → contributes 0.26 * 0.25 = 0.065 portfolio vol
MeanRev (20% vol): 31% capital → contributes 0.31 * 0.20 = 0.062 portfolio vol

Portfolio vol ≈ 0.16 (16%)  ← Lower volatility!
Sharpe ratio: 1.25          ← Better risk-adjusted returns!
```

**Advantages:**
- ✅ Risk-aware allocation (reduces volatile strategy exposure)
- ✅ Improves Sharpe ratio
- ✅ Standard in institutional portfolios

**Disadvantages:**
- ⚠️ Requires accurate volatility estimates (30+ day lookback)
- ⚠️ Backward-looking (past vol may not predict future)
- ⚠️ Can penalize high-conviction strategies during temporary vol spikes

### 3. Equal Weight Method (Baseline)

**Use case:** Simple baseline for comparison, or strategies with similar Sharpe ratios

**How it works:**

```python
# Step 1: Normalize each strategy's weights to sum to 1.0

alpha_baseline_weights = {
    'AAPL': 0.6,
    'MSFT': 0.4
}  # Sum = 1.0

momentum_weights = {
    'GOOGL': 0.7,
    'TSLA': 0.3
}  # Sum = 1.0

# Step 2: Each strategy contributes equally (1/N)
# With 2 strategies: each gets 50%

blended_weights = {
    'AAPL': 0.6 * 0.5 = 0.3,
    'MSFT': 0.4 * 0.5 = 0.2,
    'GOOGL': 0.7 * 0.5 = 0.35,
    'TSLA': 0.3 * 0.5 = 0.15
}  # Sum = 1.0
```

**Advantages:**
- ✅ Simplest method (no parameters)
- ✅ No estimation risk
- ✅ Good baseline for comparison

**Disadvantages:**
- ⚠️ Ignores strategy quality (Sharpe ratio, win rate)
- ⚠️ Ignores strategy risk (volatility)
- ⚠️ Usually sub-optimal vs rank aggregation or inverse vol

## Safety Constraints

### Per-Strategy Concentration Limits

**Problem:** Without limits, one strategy could dominate portfolio

**Example:**
```python
# Bad: No concentration limits
alpha_baseline → recommends 8 stocks, total 80% of portfolio
momentum → recommends 2 stocks, total 20% of portfolio

# Single point of failure: If alpha_baseline fails, lose 80%!
```

**Solution:** `per_strategy_max=0.40` (40% cap per strategy)

```python
allocator = MultiAlphaAllocator(per_strategy_max=0.40)

# Now:
alpha_baseline → capped at 40% (scaled down from 80%)
momentum → stays at 20%

# After renormalization:
alpha_baseline → 40 / (40+20) = 66.7%
momentum → 20 / (40+20) = 33.3%
```

**How the cap works:**
1. Calculate total contribution from each strategy across ALL symbols
2. If total > `per_strategy_max`, scale down proportionally
3. Renormalize final weights to sum to 100%

**Critical note:** Cap is **relative** (pre-normalization), not absolute. After renormalization, weights adjust to sum to 100%.

**Recommended settings:**
- **Conservative:** `per_strategy_max=0.30` (30% max, high diversification)
- **Balanced:** `per_strategy_max=0.40` (40% max, standard in quant funds)
- **Aggressive:** `per_strategy_max=0.50` (50% max, less diversification)

**Rule of thumb:** Lower cap if strategies are highly correlated; raise if uncorrelated.

### Correlation Monitoring

**Problem:** Highly correlated strategies provide little diversification benefit

**Example:**
```python
# Both strategies bet on the same stocks (high overlap)
alpha_baseline → AAPL +10%, MSFT +8%, GOOGL +6%
momentum → AAPL +12%, MSFT +7%, GOOGL +5%

# Correlation = 0.95 (almost identical!)
# No diversification benefit - essentially running same strategy twice
```

**Solution:** `check_correlation()` monitors and alerts

```python
allocator = MultiAlphaAllocator(correlation_threshold=0.70)

# Monitor correlation monthly
correlations = allocator.check_correlation({
    'alpha_baseline': returns_df,
    'momentum': returns_df
})

# If correlation > 0.70, emits WARNING log:
# "High inter-strategy correlation detected: alpha_baseline ↔ momentum = 0.95"
```

**Why 0.70 threshold?**
- Finance research: correlation >0.7 indicates high redundancy
- Correlation 0.7 → strategies share ~50% of variance
- Institutional funds typically use 0.6-0.8 threshold

**Known limitation (current implementation):**
- Implementation uses `abs(correlation) > threshold`, so both +0.8 (redundant) and -0.8 (diversifying) trigger alerts
- **False positive:** Negative correlation (-0.8) means strategies move opposite directions (good diversification!), but currently triggers alert
- **Workaround:** Manually inspect correlation sign in logs before taking action
- **Future fix:** Should only alert on positive correlation > threshold

**What to do if correlation > threshold:**
1. **Investigate:** Are strategies betting on same stocks/factors?
2. **Options:**
   - Reduce allocation to one strategy (prefer higher Sharpe)
   - Disable one strategy if correlation persists
   - Tune strategy parameters to reduce overlap

**Recommended settings:**
- **Strict:** `correlation_threshold=0.60` (catch high correlation early)
- **Balanced:** `correlation_threshold=0.70` (standard in finance)
- **Relaxed:** `correlation_threshold=0.80` (only alert on very high correlation)

## Usage Examples

### Basic: Rank Aggregation with 3 Strategies

```python
from libs.allocation import MultiAlphaAllocator
import polars as pl

# Step 1: Collect signals from each strategy
signals = {
    'alpha_baseline': pl.DataFrame({
        'symbol': ['AAPL', 'MSFT', 'GOOGL'],
        'score': [0.85, 0.70, 0.65],
        'weight': [0.4, 0.3, 0.3]
    }),
    'momentum': pl.DataFrame({
        'symbol': ['AAPL', 'GOOGL', 'TSLA'],
        'score': [0.75, 0.90, 0.60],
        'weight': [0.35, 0.40, 0.25]
    }),
    'mean_reversion': pl.DataFrame({
        'symbol': ['TSLA', 'NVDA'],
        'score': [0.80, 0.70],
        'weight': [0.60, 0.40]
    })
}

# Step 2: Create allocator with safety constraints
allocator = MultiAlphaAllocator(
    method='rank_aggregation',  # Rank-based blending
    per_strategy_max=0.40,       # Max 40% per strategy
    correlation_threshold=0.70   # Alert if corr > 70%
)

# Step 3: Allocate across strategies
blended_df = allocator.allocate(signals, strategy_stats={})

print(blended_df)
# Output:
# ┌────────┬──────────────┬────────────────────────┐
# │ symbol ┆ final_weight ┆ contributing_strategies │
# ├────────┼──────────────┼────────────────────────┤
# │ AAPL   ┆ 0.35         ┆ [alpha_baseline, mom…] │
# │ GOOGL  ┆ 0.28         ┆ [alpha_baseline, mom…] │
# │ MSFT   ┆ 0.15         ┆ [alpha_baseline]       │
# │ TSLA   ┆ 0.18         ┆ [momentum, mean_rev…]  │
# │ NVDA   ┆ 0.04         ┆ [mean_reversion]       │
# └────────┴──────────────┴────────────────────────┘

# Step 4: Monitor correlation (monthly)
recent_returns = {
    'alpha_baseline': pl.DataFrame({'date': [...], 'return': [...]}),
    'momentum': pl.DataFrame({'date': [...], 'return': [...]})
    # ... for all strategies
}

correlations = allocator.check_correlation(recent_returns)
# Alerts automatically if any pair > 0.70
```

### Advanced: Inverse Volatility with Risk Stats

```python
# Step 1: Calculate strategy volatilities (from recent 30-day returns)
strategy_stats = {
    'alpha_baseline': {
        'vol': 0.15,      # 15% annualized volatility
        'sharpe': 1.2
    },
    'momentum': {
        'vol': 0.25,      # 25% annualized volatility (more volatile!)
        'sharpe': 0.8
    },
    'mean_reversion': {
        'vol': 0.20,      # 20% annualized volatility
        'sharpe': 1.0
    }
}

# Step 2: Create allocator with inverse_vol method
allocator = MultiAlphaAllocator(
    method='inverse_vol',      # Risk-aware allocation
    per_strategy_max=0.40
)

# Step 3: Allocate (requires strategy_stats for inverse_vol)
blended_df = allocator.allocate(signals, strategy_stats)

# Momentum gets LESS allocation (25% vol) than alpha_baseline (15% vol)
# Improves risk-adjusted returns (Sharpe ratio)
```

### Orchestrator Integration

```python
# apps/orchestrator/orchestrator.py

async def run(
    self,
    symbols: list[str],
    strategy_id: str | list[str],  # Now accepts multiple strategies!
    ...
) -> OrchestrationResult:
    """Execute orchestration with single or multi-strategy mode."""

    # Example: Multi-strategy mode
    strategy_ids = ['alpha_baseline', 'momentum', 'mean_reversion']

    # Fetch signals from each strategy
    signal_responses = {}
    for sid in strategy_ids:
        response = await signal_client.fetch_signals(symbols, strategy_id=sid)
        signal_responses[sid] = response

    # Convert Signal objects → Polars DataFrames
    signal_dfs = {
        sid: signals_to_dataframe(resp.signals)
        for sid, resp in signal_responses.items()
    }

    # Allocate across strategies
    allocator = MultiAlphaAllocator(
        method='rank_aggregation',
        per_strategy_max=0.40
    )
    blended_df = allocator.allocate(signal_dfs, strategy_stats={})

    # Convert back to Signal objects for execution
    final_signals = dataframe_to_signals(blended_df)

    # Execute blended signals
    execution_result = await execution_gateway.execute(final_signals)
```

## When to Use Each Method

### Decision Tree

**Start here:**
```
Do you have reliable strategy volatility estimates (30+ days)?
├─ NO → Use rank_aggregation (default, most robust)
└─ YES
    ├─ Are strategies from different families (value, momentum, mean reversion)?
    │   └─ YES → Use rank_aggregation (handles different score scales)
    └─ Are strategies similar (all momentum variants)?
        └─ YES → Use inverse_vol (risk-aware allocation)
```

**Special cases:**
- **Baseline comparison:** Use `equal_weight` to benchmark against smarter methods
- **High correlation (>0.7):** Consider disabling one strategy instead of allocating
- **Unstable volatility:** Use `rank_aggregation` instead of `inverse_vol` (avoid penalizing temporary spikes)

### Recommended Defaults

For most users:
```python
allocator = MultiAlphaAllocator(
    method='rank_aggregation',     # Most robust
    per_strategy_max=0.40,          # Standard cap
    correlation_threshold=0.70      # Standard threshold
)
```

For risk-focused users (e.g., Sharpe maximization):
```python
allocator = MultiAlphaAllocator(
    method='inverse_vol',           # Risk-aware
    per_strategy_max=0.40,
    correlation_threshold=0.70
)
# Requires strategy_stats with 'vol' key!
```

For research/testing:
```python
# Test all three methods in backtest
for method in ['rank_aggregation', 'inverse_vol', 'equal_weight']:
    allocator = MultiAlphaAllocator(method=method)
    backtest_results = run_backtest(allocator)
    compare_sharpe_ratios(backtest_results)
```

## Common Mistakes

### Mistake 1: Not Monitoring Correlation

**Problem:**
```python
# You add a new strategy without checking correlation
strategies = ['alpha_baseline', 'momentum', 'momentum_v2']
# momentum and momentum_v2 are 95% correlated!
# → No diversification benefit, wasted compute
```

**Solution:**
```python
# ALWAYS check correlation before adding new strategy
correlations = allocator.check_correlation(recent_returns)
if any(corr > 0.70 for corr in correlations.values()):
    print("WARNING: High correlation detected, investigate before deployment")
```

### Mistake 2: Equal Weight with Different Volatilities

**Problem:**
```python
# Equal weight with mismatched volatilities
strategies = {
    'low_vol_strategy': 10% annualized vol,
    'high_vol_strategy': 30% annualized vol
}
# Equal weight → high_vol dominates portfolio risk!
```

**Solution:**
```python
# Use inverse_vol instead of equal_weight when volatilities differ
allocator = MultiAlphaAllocator(method='inverse_vol')
# Now high_vol gets less allocation
```

### Mistake 3: Per-Strategy Cap Too High

**Problem:**
```python
# Cap too high → single strategy dominates
allocator = MultiAlphaAllocator(per_strategy_max=0.80)
# One strategy can get 80% of capital → not diversified!
```

**Solution:**
```python
# Use 30-40% cap for meaningful diversification
allocator = MultiAlphaAllocator(per_strategy_max=0.40)
# Forces allocation across multiple strategies
```

### Mistake 4: Using inverse_vol Without Volatility Estimates

**Problem:**
```python
# Forget to provide strategy_stats for inverse_vol
allocator = MultiAlphaAllocator(method='inverse_vol')
result = allocator.allocate(signals, strategy_stats={})  # Empty!
# → ValueError: strategy_stats required for inverse_vol method
```

**Solution:**
```python
# ALWAYS provide strategy_stats when using inverse_vol
strategy_stats = calculate_recent_volatility(returns, lookback=30)
result = allocator.allocate(signals, strategy_stats)
```

## Testing in Backtests

### Compare Allocation Methods

```python
# Run backtest with all three methods
methods = ['rank_aggregation', 'inverse_vol', 'equal_weight']
results = {}

for method in methods:
    allocator = MultiAlphaAllocator(method=method)
    backtest = run_backtest(
        strategies=['alpha_baseline', 'momentum', 'mean_reversion'],
        allocator=allocator,
        start_date='2020-01-01',
        end_date='2024-12-31'
    )
    results[method] = backtest

# Compare metrics
for method, backtest in results.items():
    print(f"{method}:")
    print(f"  Sharpe: {backtest.sharpe:.2f}")
    print(f"  Max DD: {backtest.max_drawdown:.1%}")
    print(f"  Annual Return: {backtest.annual_return:.1%}")

# Typical results:
# rank_aggregation:
#   Sharpe: 1.45
#   Max DD: -12%
#   Annual Return: 18%
#
# inverse_vol:
#   Sharpe: 1.60   ← Best risk-adjusted returns!
#   Max DD: -10%   ← Smallest drawdown
#   Annual Return: 16%
#
# equal_weight:
#   Sharpe: 1.30
#   Max DD: -15%
#   Annual Return: 17%
```

### Monitor Correlation Over Time

```python
# Track correlation monthly in backtest
correlations_over_time = []

for month in backtest_months:
    recent_returns = get_strategy_returns(month, lookback=30)
    corr = allocator.check_correlation(recent_returns)
    correlations_over_time.append(corr)

# Plot correlation time series
plot_correlation_heatmap(correlations_over_time)

# If correlation increasing over time → strategies converging → reduce allocation
```

## Performance Characteristics

**Benchmarked performance (realistic scenario):**
- 10 strategies × 100 symbols: ~200ms
- 3 strategies × 50 symbols: ~50ms
- 5 strategies × 200 symbols: ~350ms

**Target:** <500ms for 10 strategies × 100 symbols

**Why Polars?**
- 10-100x faster than pandas for group operations
- Lazy evaluation (optimizes query plan)
- Parallel execution (uses all CPU cores)
- Memory efficient (columnar format)

## Further Reading

**Academic Research:**
- DeMiguel et al. (2009): "Optimal Versus Naive Diversification" - Equal weight often beats complex optimization
- Kritzman et al. (2010): "Regime Shifts: Implications for Dynamic Strategies" - Adapt allocation to market regimes
- Lopez de Prado (2016): "Building Diversified Portfolios that Outperform Out of Sample" - Hierarchical risk parity

**Practical Guides:**
- "Quantitative Portfolio Management" by Michael Isichenko - Multi-strategy allocation in practice
- "Active Portfolio Management" by Grinold & Kahn - Information ratio maximization

**Related Docs:**
- [ADR-0016: Multi-Alpha Allocation Methodology](../ADRs/0016-multi-alpha-allocation.md) - Design decisions
- [Feature Parity](./feature-parity.md) - Ensuring research/production consistency
- [Risk Management](./risk-management.md) - Position limits and circuit breakers

## Summary

**Key Takeaways:**

1. **Multi-alpha = combining multiple strategies** to reduce risk and improve returns
2. **Three allocation methods:**
   - **Rank aggregation:** Default, most robust (handles different score scales)
   - **Inverse volatility:** Risk-aware (reduces volatile strategy allocation)
   - **Equal weight:** Simple baseline (for comparison)
3. **Safety constraints:**
   - **Per-strategy caps:** Prevent over-concentration (default 40% max)
   - **Correlation monitoring:** Detect redundant strategies (alert if >70%)
4. **Start simple:** Use `rank_aggregation` with 40% caps
5. **Monitor correlation:** Check monthly, investigate if >0.70
6. **Backtest all methods:** Compare Sharpe ratios to pick best for your strategies

**Next Steps:**
- Implement P1T6 (Advanced Strategies) to get multiple strategies
- Backtest allocation methods with historical data
- Monitor live correlation and tune parameters
- Read ADR-0016 for detailed design decisions
