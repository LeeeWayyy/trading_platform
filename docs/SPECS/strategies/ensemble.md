# Ensemble Strategy

## Identity
- **Type:** Strategy
- **Port:** N/A
- **Container:** N/A

## Interface
### For Strategies: Signal Generation Interface
| Function | Input | Output | Description |
|----------|-------|--------|-------------|
| `combine_signals(signals, config, weights?, confidences?)` | Polars DataFrame + config | Polars DataFrame | Combines per-strategy signals into ensemble signal. |
| `CombinationMethod` | Enum | N/A | Supported combination methods (weighted, vote, unanimous, confidence-weighted, max-confidence). |
| `EnsembleConfig.validate()` | Config | None | Validates weights, thresholds, and required strategies. |
| `AdaptiveWeightConfig.validate()` | Config | None | Validates adaptive weighting parameters. |
- **Model Type:** N/A
- **Feature Set:** N/A (depends on constituent strategies)
- **Retraining Frequency:** N/A

## Behavioral Contracts
> **Purpose:** Enable AI coders to understand WHAT the code does without reading source.

### Key Functions (detailed behavior)
#### combine_signals(...)
**Purpose:** Merge strategy signals into a single ensemble signal per symbol/date.

**Preconditions:**
- Input DataFrame includes `symbol`, `date`, and one column per strategy (e.g., `mean_reversion`, `momentum`).
- `EnsembleConfig` is valid (`strategy_weights` sum to 1.0).

**Postconditions:**
- Returns DataFrame with `ensemble_signal` and optional `ensemble_confidence`.

**Behavior:**
1. Validates input schema and configuration.
2. Applies selected combination method.
3. Enforces `min_confidence`, `min_strategies`, and `require_agreement` filters.

**Raises:**
- `ValueError`: on invalid weights, thresholds, or missing strategy columns.

### Invariants
- Strategy weights (if provided) must sum to 1.0.
- Output signal is discrete: -1, 0, +1.

### State Machine (if stateful)
```
[Input] --> [Validated] --> [Combined] --> [Filtered]
```
- **States:** Input, Validated, Combined, Filtered
- **Transitions:** combine_signals validates and computes output.

## Data Flow
```
Strategy signals --> Validate --> Combine --> Filter --> Ensemble signal
```
- **Input format:** Polars DataFrame with per-strategy signals.
- **Output format:** Polars DataFrame with `ensemble_signal` (+ confidence).
- **Side effects:** None.

## Usage Examples
### Example 1: Weighted average
```python
from strategies.ensemble.combiner import combine_signals
from strategies.ensemble.config import EnsembleConfig

config = EnsembleConfig()
result = combine_signals(signals_df, config)
```

### Example 2: Majority vote
```python
from strategies.ensemble.combiner import combine_signals, CombinationMethod
from strategies.ensemble.config import EnsembleConfig

config = EnsembleConfig(combination_method=CombinationMethod.MAJORITY_VOTE)
result = combine_signals(signals_df, config)
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Missing strategy column | Missing `momentum` | Raises `ValueError`. |
| Weights not summing to 1 | `sum(weights) != 1` | Raises `ValueError`. |
| Conflicting signals | +1 and -1 | Filtered to 0 if `require_agreement=True`. |

## Dependencies
- **Internal:** `strategies/alpha_baseline` (production), `research/strategies/mean_reversion`, `research/strategies/momentum` (experimental)
- **External:** polars

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `combination_method` | Yes | `weighted_average` | Combination algorithm.
| `strategy_weights` | No | `{mean_reversion:0.5, momentum:0.5}` | Weights per strategy.
| `min_confidence` | No | `0.6` | Minimum confidence to act.
| `signal_threshold` | No | `0.3` | Threshold for weighted average.
| `min_strategies` | No | `2` | Minimum strategies required.
| `require_agreement` | No | `false` | Enforce no conflicting signals.

## Error Handling
- Raises `ValueError` for invalid weights or missing columns.

## Observability (Services only)
### Health Check
- **Endpoint:** N/A
- **Checks:** N/A

### Metrics
| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| N/A | - | - | Offline strategy. |

## Security
- **Auth Required:** No
- **Auth Method:** None
- **Data Sensitivity:** Internal
- **RBAC Roles:** N/A

## Testing
- **Test Files:** `tests/strategies/ensemble/`
- **Run Tests:** `pytest tests/strategies/ensemble -v`
- **Coverage:** N/A

## Related Specs
- `mean_reversion.md`
- `momentum.md`
- `backtest.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-03
- **Source Files:** `strategies/ensemble/*.py`
- **ADRs:** N/A
