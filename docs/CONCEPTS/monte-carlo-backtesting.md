# Monte Carlo Backtesting

Monte Carlo simulation stress-tests a backtest by resampling the observed daily portfolio returns and IC series to understand how sensitive the results are to randomness.

## Methods
- **Bootstrap (with replacement):** Draws daily returns randomly with replacement. Preserves distributional properties but breaks temporal order. Good for estimating variance of metrics under the same return distribution.
- **Shuffle (without replacement):** Permutes the return path. Keeps the exact set of returns but randomizes order. Good for asking “does sequencing matter?” (path dependency).

## Confidence Intervals
- Computed from the simulated metric distributions at the 5th, 50th, and 95th percentiles.
- Interpret as: “Given the resampling method, 90% of simulated metrics fall between lower_5 and upper_95.”
- Invariant check: `lower_5 <= median <= upper_95` should always hold (guarded by tests).

## P-Value (one-sided)
- Defined as `p_value = count(simulated_metric >= observed_metric) / n_simulations`.
- **Bootstrap p-value**: Measures the probability of exceeding the observed Sharpe under resampling. Values near 0.5 are expected since bootstrap preserves the sample mean. Use primarily for understanding metric variance, not as a "no skill" test.
- **Shuffle p-value**: Tests path dependency - a small p-value (<0.05) suggests the observed Sharpe is unlikely under random ordering, indicating the return sequence matters (potential time-dependence or momentum effects).
- Bounded in `[0, 1]` by construction (tested).

## Example Usage
```python
from libs.trading.backtest import MonteCarloConfig, MonteCarloSimulator

config = MonteCarloConfig(n_simulations=1000, method="bootstrap", random_seed=42)
simulator = MonteCarloSimulator(config)

# Option 1: Use run() which dispatches based on config.method
mc_result = simulator.run(backtest_result)

# Option 2: Call methods directly
mc_result = simulator.run_bootstrap(backtest_result)  # or run_shuffle(...)

print("Sharpe 90% CI:", mc_result.sharpe_ci.lower_5, mc_result.sharpe_ci.upper_95)
print("P-value (Sharpe):", mc_result.p_value_sharpe)
```

## Operational Notes
- Uses NumPy’s `default_rng(seed)` for reproducibility; a warning is logged if `random_seed` is `None`.
- Inputs come from `BacktestResult.daily_portfolio_returns` (schema: `{date: pl.Date, return: pl.Float64}`) and `daily_ic`.
- Designed to complete 1,000 simulations on a 500-day backtest in under 10 seconds (enforced by tests).
