# analytics

## Identity
- **Type:** Library
- **Port:** N/A
- **Container:** N/A

## Interface
### Public Interface (Exported Classes & Functions)
| Class/Function | Parameters | Returns | Description |
|----------------|------------|---------|-------------|
| `MicrostructureAnalyzer` | config | instance | VPIN/spread/realized vol analytics. |
| `HARVolatilityModel` | config | instance | HAR volatility forecasting. |
| `EventStudyFramework` | config | instance | CAR/PEAD/event study analysis. |
| `FactorAttribution` | config | instance | Fama-French attribution. |

## Behavioral Contracts
### EventStudyFramework.run_event_study(...)
**Purpose:** Compute event-study statistics with overlap handling.

### Invariants
- Results include dataset version metadata when available.

## Data Flow
```
input returns -> model/analysis -> metrics/attribution results
```
- **Input format:** return series, event windows, factor data.
- **Output format:** result objects (CAR, exposures, forecasts).
- **Side effects:** None.

## Usage Examples
### Example 1: Event study
```python
from libs.analytics import EventStudyFramework, EventStudyConfig

framework = EventStudyFramework(EventStudyConfig(...))
result = framework.run_event_study(...)
```

### Example 2: Factor attribution
```python
from libs.analytics import FactorAttribution, FactorAttributionConfig

attr = FactorAttribution(FactorAttributionConfig(...))
report = attr.run(...)
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Insufficient observations | short series | `InsufficientObservationsError`. |
| PIT violation | look-ahead data | `PITViolationError`. |
| Data mismatch | inconsistent indexes | `DataMismatchError`. |

## Dependencies
- **Internal:** `libs.data_quality` (versioning where used)
- **External:** numpy/pandas/statsmodels (analysis stack)

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| N/A | - | - | Configuration via config classes. |

## Error Handling
- Raises `FactorAttributionError` and related exceptions on invalid inputs.

## Security
- N/A (analytics library).

## Testing
- **Test Files:** `tests/libs/analytics/`
- **Run Tests:** `pytest tests/libs/analytics -v`
- **Coverage:** N/A

## Related Specs
- `data_quality.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-03
- **Source Files:** `libs/analytics/__init__.py`, `libs/analytics/event_study.py`, `libs/analytics/attribution.py`
- **ADRs:** N/A
