# backtest

<!-- Last reviewed: 2026-01-30 - TradeCost.symbol→identifier, compute_cost_summary/capacity_analysis use trades_df -->

## Identity
- **Type:** Library
- **Port:** N/A
- **Container:** N/A

## Interface
### Public Interface (Exported Classes & Functions)
| Class/Function | Parameters | Returns | Description |
|----------------|------------|---------|-------------|
| `BacktestJob` | fields | model | Backtest job metadata. |
| `BacktestJobQueue` | config | instance | RQ-backed job queue (optional). |
| `BacktestWorker` | queues | instance | RQ worker wrapper (optional). |
| `MonteCarloSimulator` | config | instance | Monte Carlo backtest analysis. |
| `WalkForwardOptimizer` | config | instance | Walk-forward optimization. |
| `grid_search` | params | `SearchResult` | Parameter grid search. |
| `random_search` | params | `SearchResult` | Random parameter search. |
| `CostModelConfig` | fields | config | Transaction cost model configuration. |
| `TradeCost` | fields | dataclass | Cost breakdown for a single trade (identifier, not symbol). |
| `compute_backtest_costs` | weights, returns, adv | `BacktestCostResult` | Compute costs for a backtest. |
| `compute_cost_summary` | returns, trades_df, value | `CostSummary` | Aggregate cost statistics from trades DataFrame. |
| `compute_capacity_analysis` | weights, trades_df, summary | `CapacityAnalysis` | Analyze strategy capacity constraints. |

## Behavioral Contracts
### run_backtest(...)
**Purpose:** Execute a backtest job (RQ optional).

### Invariants
- Missing `rq` dependency disables queue/worker exports.

## Data Flow
```
BacktestJob -> worker/runner -> result storage
```
- **Input format:** job configs and parameter grids.
- **Output format:** results stored in paths or objects.
- **Side effects:** file writes, optional DB/Redis (via RQ).

## Usage Examples
### Example 1: Grid search
```python
from libs.trading.backtest import grid_search

result = grid_search(param_grid={"lookback": [20, 60]}, evaluator=...)
```

### Example 2: Monte Carlo simulation
```python
from libs.trading.backtest import MonteCarloSimulator, MonteCarloConfig

sim = MonteCarloSimulator(MonteCarloConfig(...))
report = sim.run(...)
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Missing rq | rq not installed | queue/worker exports are None. |
| Missing result path | invalid path | `ResultPathMissing` raised. |
| Job not found | unknown id | `JobNotFound` raised. |

## Dependencies
- **Internal:** N/A
- **External:** rq (optional), structlog (optional)

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| N/A | - | - | Configuration via class constructors. |

## Error Handling
- Uses `JobNotFound`, `ResultPathMissing` for missing artifacts.

## Security
- N/A (analysis library).

## Testing
- **Test Files:** `tests/libs/trading/backtest/`
- **Run Tests:** `pytest tests/libs/backtest -v`
- **Coverage:** N/A

## Related Specs
- `../services/backtest_worker.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-11
- **Source Files:** `libs/trading/backtest/__init__.py`, `libs/trading/backtest/job_queue.py`, `libs/trading/backtest/worker.py`
- **ADRs:** N/A
