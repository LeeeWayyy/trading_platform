# backtest

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
from libs.backtest import grid_search

result = grid_search(param_grid={"lookback": [20, 60]}, evaluator=...)
```

### Example 2: Monte Carlo simulation
```python
from libs.backtest import MonteCarloSimulator, MonteCarloConfig

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
- **Test Files:** `tests/libs/backtest/`
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
- **Source Files:** `libs/backtest/__init__.py`, `libs/backtest/job_queue.py`, `libs/backtest/worker.py`
- **ADRs:** N/A
