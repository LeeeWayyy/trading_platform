# analytics

<!-- Last reviewed: 2026-02-09 - P6T12: Added libs/analytics shared module (metrics, live_vs_backtest) -->

## Identity
- **Type:** Library
- **Port:** N/A
- **Container:** N/A

## Interface
### Public Interface (Exported Classes & Functions)

#### libs/platform/analytics (Microstructure & Attribution)
| Class/Function | Parameters | Returns | Description |
|----------------|------------|---------|-------------|
| `MicrostructureAnalyzer` | config | instance | VPIN/spread/realized vol analytics. |
| `HARVolatilityModel` | config | instance | HAR volatility forecasting. |
| `EventStudyFramework` | config | instance | CAR/PEAD/event study analysis. |
| `FactorAttribution` | config | instance | Fama-French attribution. |

#### libs/analytics (Shared Analytics - P6T12)
| Class/Function | Parameters | Returns | Description |
|----------------|------------|---------|-------------|
| `compute_tracking_error` | returns_a, returns_b, *, pre_aligned=False | `float \| None` | Annualized tracking error between two return series. Inner-join or pre-aligned mode. |
| `LiveVsBacktestAnalyzer` | config: OverlayConfig \| None | instance | Compare live trading performance against backtest expectations. Pure data-in/data-out. |
| `OverlayConfig` | thresholds, window | BaseModel | Configuration for live vs backtest overlay analysis (TE threshold, divergence threshold, rolling window). |
| `OverlayResult` | cumulative curves, metrics, alerts | dataclass | Output of `LiveVsBacktestAnalyzer.analyze()` with alert level and message. |
| `AlertLevel` | - | Enum | Severity levels: NONE, YELLOW, RED. |

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
from libs.platform.analytics import EventStudyFramework, EventStudyConfig

framework = EventStudyFramework(EventStudyConfig(...))
result = framework.run_event_study(...)
```

### Example 2: Factor attribution
```python
from libs.platform.analytics import FactorAttribution, FactorAttributionConfig

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
- **Test Files:** `tests/libs/platform/analytics/`, `tests/libs/analytics/`
- **Run Tests:** `pytest tests/libs/analytics -v`
- **Coverage:** N/A

## Related Specs
- `data_quality.md`
- `web_console_ng.md` - Backtest comparison chart consumes tracking error and live vs backtest overlay

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-02-09 (P6T12 - Added libs/analytics shared module with compute_tracking_error, LiveVsBacktestAnalyzer)
- **Source Files:** `libs/platform/analytics/__init__.py`, `libs/platform/analytics/event_study.py`, `libs/platform/analytics/attribution.py`, `libs/analytics/__init__.py`, `libs/analytics/metrics.py`, `libs/analytics/live_vs_backtest.py`
- **ADRs:** N/A
