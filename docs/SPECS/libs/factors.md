# factors

## Identity
- **Type:** Library
- **Port:** N/A
- **Container:** N/A

## Interface
### Public Interface (Exported Classes & Functions)
| Class/Function | Parameters | Returns | Description |
|----------------|------------|---------|-------------|
| `FactorBuilder` | providers, manifest | builder | Compute factor exposures with PIT correctness. |
| `FactorConfig` | settings | model | Factor computation configuration. |
| `FactorDefinition` | - | protocol | Interface for custom factor definitions. |
| `FactorResult` | fields | model | Factor exposures + metadata. |
| `FactorAnalytics` | data | analyzer | IC analysis, decay, correlations. |
| `ICAnalysis` | metrics | model | Information coefficient outputs. |
| `MomentumFactor` | params | factor | Canonical momentum factor. |
| `BookToMarketFactor` | params | factor | Canonical value factor. |
| `ROEFactor` | params | factor | Canonical quality factor. |
| `SizeFactor` | params | factor | Canonical size factor. |
| `RealizedVolFactor` | params | factor | Canonical low-vol factor. |
| `CANONICAL_FACTORS` | - | dict | Registry of built-in factor definitions. |
| `DiskExpressionCache` | path | cache | Cache for factor expressions. |
| `CacheError` | message | exception | Cache error. |
| `CacheCorruptionError` | message | exception | Cache corruption error. |

## Behavioral Contracts
### FactorBuilder.compute_factor(...)
**Purpose:** Compute factor exposures for a given date with PIT-correct inputs.

**Preconditions:**
- Required datasets available from providers.
- Manifest entries exist for relevant data snapshots.

**Postconditions:**
- Returns `FactorResult` with exposures and metadata.

**Behavior:**
1. Load inputs from data providers with PIT alignment.
2. Apply factor definition computation.
3. Validate and normalize exposures.
4. Persist or cache intermediate outputs if configured.

**Raises:**
- Provider/data errors propagate to caller.

### FactorAnalytics.compute_ic(...)
**Purpose:** Compute IC, decay, and correlations for factor evaluation.

**Preconditions:**
- Factor exposures and future returns aligned by date/symbol.

**Postconditions:**
- Returns `ICAnalysis` with summary metrics.

**Behavior:**
1. Align exposures/returns.
2. Compute IC by date.
3. Aggregate statistics and decay metrics.

**Raises:**
- Value errors on misaligned inputs.

### Invariants
- All factor computations are point-in-time correct.
- Factor definitions are deterministic for a given snapshot.

### State Machine (if stateful)
```
[Inputs Ready] --> [Computed] --> [Analyzed]
      |                 |
      +-----------------+ (recompute)
```
- **States:** inputs ready, computed, analyzed.
- **Transitions:** recompute allowed with new configs.

## Data Flow
```
providers -> factor inputs -> factor exposures -> analytics
```
- **Input format:** Provider datasets, factor config.
- **Output format:** `FactorResult`, `ICAnalysis`.
- **Side effects:** Optional cache writes.

## Usage Examples
### Example 1: Compute factor exposures
```python
from libs.factors import FactorBuilder

result = builder.compute_factor("momentum_12_1", as_of_date)
```

### Example 2: Analyze factor IC
```python
from libs.factors import FactorAnalytics

ic = FactorAnalytics().compute_ic(exposures, forward_returns)
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Missing data | provider gap | Raises error; no silent fill |
| Cache corruption | bad cache entry | `CacheCorruptionError` raised |
| Non-trading date | holiday | Aligns to trading calendar or errors |

## Dependencies
- **Internal:** `libs.data_quality`, `libs.data_providers`
- **External:** Pandas/Polars/Numpy (analysis stack)

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| N/A | - | - | Configuration passed via `FactorConfig`. |

## Error Handling
- Cache errors surfaced as `CacheError`/`CacheCorruptionError`.
- Data provider errors propagate.

## Observability (Services only)
### Health Check
- **Endpoint:** N/A
- **Checks:** N/A

### Metrics
| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| N/A | - | - | Library has no metrics. |

## Security
- No secrets; depends on provider credentials upstream.

## Testing
- **Test Files:** `tests/libs/factors/`
- **Run Tests:** `pytest tests/libs/factors -v`
- **Coverage:** N/A

## Related Specs
- `data_providers.md`
- `data_quality.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-09
- **Source Files:** `libs/factors/__init__.py`, `libs/factors/factor_builder.py`, `libs/factors/factor_definitions.py`, `libs/factors/factor_analytics.py`, `libs/factors/cache.py`
- **ADRs:** N/A
