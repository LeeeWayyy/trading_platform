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
    correlation_threshold=0.70      # Alert if corr > 70%
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

... (unchanged explanatory examples) ...

## How It Works

### 1. Rank Aggregation Method (Default)

... (unchanged methodology and rationale) ...

### 2. Inverse Volatility Method (Risk-Aware)

... (unchanged methodology and rationale) ...

### 3. Equal Weight Method (Baseline)

... (unchanged methodology and rationale) ...

## Safety Constraints

### Per-Strategy Concentration Limits

... (unchanged explanation) ...

### Correlation Monitoring

**Problem:** Highly correlated strategies provide little diversification benefit

**Implementation:** `check_correlation()` monitors and alerts

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

**Important implementation detail (current):** alerts are emitted only for **positive** pairwise Pearson correlation values that exceed `correlation_threshold`. Negative correlations (strong diversification) do not trigger alerts.

**Why 0.70 threshold?**
- Finance research: correlation >0.7 indicates high redundancy
- Institutional funds typically use 0.6-0.8 threshold

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

... (unchanged examples showing usage and orchestration integration) ...

## Known implementation notes and behavior

- The allocator explicitly supports a boolean `allow_short_positions` flag when initialized. When `allow_short_positions=True` the allocator preserves signed (negative) symbol weights produced by market-neutral strategies, uses GROSS exposure (sum of absolute weights) for normalization in zero-net portfolios, and enables market-neutral normalization semantics in the allocation pipeline. By default this flag is `False` to keep long-only behavior.

- `check_correlation()` requires overlapping return history between strategy pairs; pairs with insufficient overlapping dates are skipped with a warning and are not included in the returned correlation dictionary.

- Per-strategy caps are computed on GROSS contribution (sum of absolute contributions) to avoid a market-neutral strategy from bypassing caps via offsetting long/short positions.

## Further Reading

... (unchanged references and next steps) ...
