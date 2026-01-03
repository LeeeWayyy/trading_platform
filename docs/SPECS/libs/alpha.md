# alpha

## Identity
- **Type:** Library
- **Port:** N/A
- **Container:** N/A

## Interface
### Public Interface (Exported Classes & Functions)
| Class/Function | Parameters | Returns | Description |
|----------------|------------|---------|-------------|
| `AlphaDefinition` | protocol | type | Contract for alpha signal computation. |
| `PITBacktester` | dataset providers, metrics | instance | PIT-correct backtesting engine. |
| `AlphaMetricsAdapter` | `prefer_qlib` | instance | Metrics with Qlib/local fallback. |
| `MomentumAlpha` | `lookback_days`, `skip_days` | instance | Canonical momentum alpha. |
| `AlphaCombiner` | config | instance | Combine multiple alpha signals. |

## Behavioral Contracts
### PITBacktester.run_backtest(...)
**Purpose:** Run PIT-correct backtests with dataset versioning.

**Preconditions:**
- Providers enforce snapshot/lag rules.

**Postconditions:**
- Returns `BacktestResult` with metrics and metadata.

### Invariants
- PIT safety is enforced; look-ahead raises `PITViolationError`.

## Data Flow
```
alpha definition -> snapshot data -> backtest engine -> metrics result
```
- **Input format:** alpha definitions and dataset providers.
- **Output format:** backtest result objects.
- **Side effects:** None.

## Usage Examples
### Example 1: Backtest a canonical alpha
```python
from libs.alpha import PITBacktester, MomentumAlpha, AlphaMetricsAdapter

backtester = PITBacktester(...)
alpha = MomentumAlpha(lookback_days=252, skip_days=21)
result = backtester.run_backtest(alpha=alpha, start_date=..., end_date=...)
```

### Example 2: Combine alphas
```python
from libs.alpha import AlphaCombiner

combiner = AlphaCombiner(...)
combined = combiner.combine(signals)
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Look-ahead data | future data access | `PITViolationError`. |
| Missing forward returns | insufficient data | `MissingForwardReturnError`. |
| Empty universe | no symbols | returns empty result or raises. |

## Dependencies
- **Internal:** `libs.data_quality`, `libs.data_providers`
- **External:** numpy/pandas/qlib (optional)

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| N/A | - | - | Configuration via constructor arguments. |

## Error Handling
- Raises `AlphaResearchError` and specialized exceptions for data issues.

## Security
- N/A (research library).

## Testing
- **Test Files:** `tests/libs/alpha/`
- **Run Tests:** `pytest tests/libs/alpha -v`
- **Coverage:** N/A

## Related Specs
- `data_quality.md`
- `data_providers.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-03
- **Source Files:** `libs/alpha/__init__.py`, `libs/alpha/research_platform.py`, `libs/alpha/alpha_library.py`
- **ADRs:** N/A
