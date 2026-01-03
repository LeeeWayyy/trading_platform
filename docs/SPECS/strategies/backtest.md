# Backtest Strategy Evaluator

## Identity
- **Type:** Strategy
- **Port:** N/A
- **Container:** N/A

## Interface
### For Strategies: Signal Generation Interface
| Function | Input | Output | Description |
|----------|-------|--------|-------------|
| `SignalEvaluator(signals, returns, signal_column="signal", return_column="return")` | Polars DataFrames | `SignalEvaluator` | Constructs evaluator for discrete signals and returns. |
| `SignalEvaluator.evaluate(commission=0.0, risk_free_rate=0.0)` | Scalars | `dict[str, float]` | Computes performance metrics. |
| `SignalEvaluator.get_strategy_returns()` | None | `pl.Series` | Returns per-period strategy returns after evaluation. |
| `SignalEvaluator.get_cumulative_returns()` | None | `pl.Series` | Returns cumulative returns after evaluation. |
| `quick_evaluate(signals, returns, ...)` | Polars DataFrames | `dict[str, float]` | Convenience wrapper for evaluation. |
- **Model Type:** N/A
- **Feature Set:** N/A
- **Retraining Frequency:** N/A

## Behavioral Contracts
> **Purpose:** Enable AI coders to understand WHAT the code does without reading source.

### Key Functions (detailed behavior)
#### SignalEvaluator.evaluate(...)
**Purpose:** Convert signals into strategy returns and compute metrics.

**Preconditions:**
- `signals` contains `symbol`, `date`, and `signal` columns.
- `returns` contains `symbol`, `date`, and `return` columns.

**Postconditions:**
- `results` dict populated with return, Sharpe, drawdown, win rate, profit factor, trade count.
- `strategy_returns` series stored for later access.

**Behavior:**
1. Shifts return series forward by one period per symbol.
2. Joins signals with shifted returns.
3. Applies commission on non-zero signals.
4. Computes standard metrics in `strategies.backtest.metrics`.

**Raises:**
- `ValueError`: if required columns are missing.

### Invariants
- Signals are treated as discrete values in {-1, 0, +1}.
- Returns are aligned to next period to prevent look-ahead bias.

### State Machine (if stateful)
```
[Initialized] --> [Evaluated]
     |              ^
     +--------------+ (re-evaluate with new params)
```
- **States:** Initialized, Evaluated
- **Transitions:** `evaluate()` computes results.

## Data Flow
```
Signals + Returns --> Shift Returns --> Join --> Strategy Returns --> Metrics
```
- **Input format:** Polars DataFrames with `symbol`, `date`, `signal`/`return`.
- **Output format:** Metrics dict and return series.
- **Side effects:** None.

## Usage Examples
### Example 1: Evaluate signals
```python
from strategies.backtest import SignalEvaluator

results = SignalEvaluator(signals_df, returns_df).evaluate(commission=0.001)
print(results["sharpe_ratio"])
```

### Example 2: Quick evaluation
```python
from strategies.backtest.evaluator import quick_evaluate

results = quick_evaluate(signals_df, returns_df)
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Missing columns | Missing `signal` or `return` | Raises `ValueError`. |
| Evaluate before access | `get_strategy_returns()` before `evaluate()` | Raises `RuntimeError`. |
| Empty joins | No overlapping dates | Results computed on empty set (metrics may be zeros/NaN). |

## Dependencies
- **Internal:** None
- **External:** polars

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `signal_column` | No | `signal` | Signal column name.
| `return_column` | No | `return` | Return column name.
| `commission` | No | `0.0` | Per-trade commission.
| `risk_free_rate` | No | `0.0` | Annual risk-free rate.

## Error Handling
- Raises `ValueError` for missing columns.
- Raises `RuntimeError` if accessing returns before evaluation.

## Observability (Services only)
### Health Check
- **Endpoint:** N/A
- **Checks:** N/A

### Metrics
| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| N/A | - | - | Offline evaluator. |

## Security
- **Auth Required:** No
- **Auth Method:** None
- **Data Sensitivity:** Internal
- **RBAC Roles:** N/A

## Testing
- **Test Files:** `tests/strategies/backtest/`
- **Run Tests:** `pytest tests/strategies/backtest -v`
- **Coverage:** N/A

## Related Specs
- `ensemble.md`
- `mean_reversion.md`
- `momentum.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-03
- **Source Files:** `strategies/backtest/*.py`
- **ADRs:** N/A
