# Mean Reversion Strategy

## Identity
- **Type:** Strategy
- **Port:** N/A
- **Container:** N/A

## Interface
### For Strategies: Signal Generation Interface
| Function | Input | Output | Description |
|----------|-------|--------|-------------|
| `compute_mean_reversion_features(prices, config?)` | Polars DataFrame | Polars DataFrame | Computes RSI/Bollinger/Stochastic/Z-score features. |
| `load_and_compute_features(data_path, config?)` | Parquet path | Polars DataFrame | Loads OHLCV data and computes features. |
- **Model Type:** LightGBM regression (per README)
- **Feature Set:** RSI, Bollinger Bands, Stochastic Oscillator, Z-score
- **Retraining Frequency:** N/A (manual/adhoc)

## Behavioral Contracts
> **Purpose:** Enable AI coders to understand WHAT the code does without reading source.

### Key Functions (detailed behavior)
#### compute_mean_reversion_features(prices, config?)
**Purpose:** Produce a feature set for mean reversion signals from OHLCV data.

**Preconditions:**
- Input includes `date`, `close`, `high`, `low`, `volume` columns.

**Postconditions:**
- Returns DataFrame with engineered indicators and no look-ahead bias.

**Behavior:**
1. Validates input schema.
2. Computes RSI, Bollinger Bands, Stochastic Oscillator, Z-score features.
3. Returns feature table aligned to input dates.

**Raises:**
- `ValueError`: if required columns are missing.

### Invariants
- Rolling windows do not use future data.
- Feature columns remain stable for model parity.

### State Machine (if stateful)
```
[Input] --> [Validated] --> [FeaturesComputed]
```
- **States:** Input, Validated, FeaturesComputed
- **Transitions:** feature computation path only.

## Data Flow
```
OHLCV --> Validate --> Indicator Computation --> Feature Table --> Model/Signals
```
- **Input format:** Polars DataFrame with OHLCV columns.
- **Output format:** Polars DataFrame with feature columns.
- **Side effects:** None.

## Usage Examples
### Example 1: Feature generation
```python
from strategies.mean_reversion.features import load_and_compute_features

features = load_and_compute_features("data/adjusted/2024-01-01/AAPL.parquet")
print(features.tail(1))
```

### Example 2: Config customization
```python
from strategies.mean_reversion.config import MeanReversionConfig

config = MeanReversionConfig()
config.features.rsi_period = 10
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Missing OHLCV columns | Data without `close` | Raises `ValueError`. |
| Short history | Fewer rows than window | Outputs nulls for initial rows. |
| NaNs in price | Missing values | Forward fill or null propagation per implementation. |

## Dependencies
- **Internal:** None
- **External:** polars, numpy

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `MeanReversionFeatureConfig.rsi_period` | No | `14` | RSI window length.
| `MeanReversionFeatureConfig.bb_period` | No | `20` | Bollinger window length.
| `MeanReversionFeatureConfig.bb_std` | No | `2.0` | Bollinger band width.
| `MeanReversionTradingConfig.*` | No | (see config) | Entry/exit thresholds + risk limits.

## Error Handling
- Raises `ValueError` for missing required columns.

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
- **Test Files:** `tests/strategies/mean_reversion/`
- **Run Tests:** `pytest tests/strategies/mean_reversion -v`
- **Coverage:** N/A

## Related Specs
- `momentum.md`
- `ensemble.md`
- `backtest.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-09
- **Source Files:** `strategies/mean_reversion/*.py`, `strategies/mean_reversion/README.md`
- **ADRs:** N/A
