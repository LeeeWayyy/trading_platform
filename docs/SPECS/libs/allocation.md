# allocation

## Identity
- **Type:** Library
- **Port:** N/A
- **Container:** N/A

## Interface
### Public Interface (Exported Classes & Functions)
| Class/Function | Parameters | Returns | Description |
|----------------|------------|---------|-------------|
| `MultiAlphaAllocator` | `method`, `per_strategy_max`, `correlation_threshold`, `allow_short_positions` | instance | Risk-aware allocator across strategies. |
| `AllocMethod` | literal | type | Allocation method: `rank_aggregation`, `inverse_vol`, `equal_weight`. |

## Behavioral Contracts
### MultiAlphaAllocator.allocate(signals, strategy_stats) -> DataFrame
**Purpose:** Blend multiple strategy signals into a single allocation.

**Preconditions:**
- `signals` must be non-empty.
- `strategy_stats` required for `inverse_vol`.

**Postconditions:**
- Returns `final_weight` per symbol, normalized.

**Behavior:**
1. Validate inputs.
2. Aggregate by method.
3. Enforce per-strategy caps and normalization.

### Invariants
- Weights are normalized after allocation.
- Per-strategy cap is enforced when configured.

## Data Flow
```
strategy signals -> allocation method -> normalization -> final weights
```
- **Input format:** dict of strategy DataFrames.
- **Output format:** DataFrame with `symbol` and `final_weight`.
- **Side effects:** None.

## Usage Examples
### Example 1: Rank aggregation
```python
from libs.trading.allocation import MultiAlphaAllocator

allocator = MultiAlphaAllocator(method="rank_aggregation")
result = allocator.allocate(signals, strategy_stats={})
```

### Example 2: Inverse volatility
```python
allocator = MultiAlphaAllocator(method="inverse_vol")
result = allocator.allocate(signals, strategy_stats={"alpha": {"vol": 0.2}})
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Empty signals | `{}` | `ValueError` raised. |
| Missing stats | `inverse_vol` without stats | `ValueError` raised. |
| Single strategy | one entry | Bypasses aggregation. |

## Dependencies
- **Internal:** N/A
- **External:** `polars`

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| N/A | - | - | Configuration via constructor only. |

## Error Handling
- Raises `ValueError` on invalid inputs or missing stats.

## Security
- N/A (pure computation).

## Testing
- **Test Files:** `tests/libs/trading/allocation/`
- **Run Tests:** `pytest tests/libs/allocation -v`
- **Coverage:** N/A

## Related Specs
- `../strategies/alpha_baseline.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-03
- **Source Files:** `libs/trading/allocation/multi_alpha.py`
- **ADRs:** N/A
