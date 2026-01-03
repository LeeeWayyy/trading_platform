# Alpha Baseline Strategy

## Identity
- **Type:** Strategy
- **Port:** N/A
- **Container:** N/A

## Interface
### For Strategies: Signal Generation Interface
| Function | Input | Output | Description |
|----------|-------|--------|-------------|
| `initialize_qlib_with_t1_data(data_dir=Path("data/adjusted"))` | T1 data directory | None | Initializes Qlib to read T1 adjusted Parquet data. |
| `get_alpha158_features(symbols, start_date, end_date, fit_start_date?, fit_end_date?, data_dir?)` | Symbols + date range | `pd.DataFrame` | Returns Alpha158 feature matrix indexed by (date, symbol). |
| `get_labels(symbols, start_date, end_date, data_dir?)` | Symbols + date range | `pd.DataFrame` | Returns next-day return labels (`LABEL0`). |
| `compute_features_and_labels(...)` | Symbols + train/valid/test ranges | Tuple of 6 `pd.DataFrame` | Convenience function to compute split features/labels. |
| `train_baseline_model(config?)` | `StrategyConfig` | `BaselineTrainer` | Trains LightGBM model and logs MLflow artifacts. |
| `evaluate_model(trainer, X_test, y_test, save_dir)` | Trained model + test data | `dict[str, float]` | Runs backtest evaluation and saves plots/reports. |
- **Model Type:** LightGBM regression (predict next-day returns)
- **Feature Set:** Qlib Alpha158
- **Retraining Frequency:** N/A (manual/adhoc via `train.py`)

## Behavioral Contracts
> **Purpose:** Enable AI coders to understand WHAT the code does without reading source.

### Key Functions (detailed behavior)
#### compute_features_and_labels(...)
**Purpose:** Generate train/valid/test feature matrices and labels from T1 adjusted data.

**Preconditions:**
- T1 adjusted Parquet files exist under `data/adjusted/`.
- Date ranges are non-overlapping and ordered.

**Postconditions:**
- Returns six DataFrames with aligned MultiIndex (date, symbol).
- Labels include `LABEL0` (next-day return) with NaNs for last day.

**Behavior:**
1. Initializes Qlib with T1 data provider.
2. Computes Alpha158 features for each split.
3. Computes labels for each split.

**Raises:**
- `RuntimeError`: if Qlib initialization is unavailable.

#### train_baseline_model(config?) -> BaselineTrainer
**Purpose:** Train a LightGBM model on Alpha158 features with MLflow tracking.

**Preconditions:**
- Config has valid data ranges and model hyperparameters.
- Features/labels can be computed for the requested date ranges.

**Postconditions:**
- Trained model is stored under `artifacts/models/` (default).
- MLflow run contains params and metrics.

**Behavior:**
1. Builds features/labels via `compute_features_and_labels`.
2. Trains LightGBM with early stopping.
3. Logs metrics and artifacts to MLflow.

**Raises:**
- `ValueError`: on invalid config.

### Invariants
- Alpha158 feature definitions remain consistent between research and production.
- Labels represent next-day returns (`LABEL0`).

### State Machine (if stateful)
```
[Uninitialized] --> [QlibInitialized] --> [Trained] --> [Evaluated]
         |                    ^
         +--------------------+ (re-init for new data dir)
```
- **States:** Uninitialized, QlibInitialized, Trained, Evaluated
- **Transitions:** `initialize_qlib_with_t1_data` initializes; training produces model; evaluation produces metrics.

## Data Flow
> How data transforms through this component

```
T1 Parquet --> Qlib Provider --> Alpha158 Features --> LightGBM Train --> Model Artifacts
                                   |                               
                                   v
                               Labels (LABEL0) --> Backtest --> Metrics/Plots
```
- **Input format:** T1 adjusted OHLCV Parquet files in `data/adjusted/<date>/<symbol>.parquet`.
- **Output format:** Model artifacts + evaluation metrics dict.
- **Side effects:** Writes to `artifacts/` and MLflow tracking store.

## Usage Examples
### Example 1: Train and evaluate
```python
from strategies.alpha_baseline.config import StrategyConfig
from strategies.alpha_baseline.train import train_baseline_model
from strategies.alpha_baseline.backtest import evaluate_model

config = StrategyConfig()
trainer = train_baseline_model(config)
results = evaluate_model(trainer, trainer.X_test, trainer.y_test, save_dir="artifacts/backtest")
print(results["sharpe_ratio"])
```

### Example 2: Feature computation
```python
from strategies.alpha_baseline.features import compute_features_and_labels

X_train, y_train, X_valid, y_valid, X_test, y_test = compute_features_and_labels(
    symbols=["AAPL", "MSFT"],
    train_start="2020-01-01", train_end="2023-12-31",
    valid_start="2024-01-01", valid_end="2024-06-30",
    test_start="2024-07-01", test_end="2024-12-31",
)
```

## Edge Cases & Boundaries
| Scenario | Input | Expected Behavior |
|----------|-------|-------------------|
| Missing data files | `data_dir` without symbol Parquet | Qlib returns empty/raises depending on Qlib backend. |
| Misordered dates | `train_start` > `train_end` | Training fails or returns empty splits. |
| Last-day label | End-of-range label | Last date has NaN label and is typically dropped. |

## Dependencies
- **Internal:** None
- **External:** qlib, pandas, lightgbm, mlflow

## Configuration
| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `StrategyConfig.data.symbols` | Yes | `["AAPL", "MSFT", "GOOGL"]` | Symbols for training.
| `StrategyConfig.data.data_dir` | Yes | `data/adjusted` | T1 adjusted data root.
| `StrategyConfig.model.*` | Yes | (see config) | LightGBM hyperparameters.
| `StrategyConfig.training.*` | Yes | (see config) | Early stopping + MLflow settings.

## Error Handling
- Raises `RuntimeError` if Qlib initialization is unavailable.
- Raises `ValueError` for invalid config values.

## Observability (Services only)
### Health Check
- **Endpoint:** N/A
- **Checks:** N/A

### Metrics
| Metric Name | Type | Labels | Description |
|-------------|------|--------|-------------|
| N/A | - | - | Strategy runs offline. |

## Security
- **Auth Required:** No
- **Auth Method:** None
- **Data Sensitivity:** Internal
- **RBAC Roles:** N/A

## Testing
- **Test Files:** `tests/strategies/alpha_baseline/`
- **Run Tests:** `pytest tests/strategies/alpha_baseline -v`
- **Coverage:** N/A

## Related Specs
- `../services/signal_service.md`
- `../libs/data_pipeline.md`

## Known Issues & TODO
| Issue | Severity | Description | Tracking |
|-------|----------|-------------|----------|
| None | - | No known issues | - |

## Metadata
- **Last Updated:** 2026-01-03
- **Source Files:** `strategies/alpha_baseline/*.py`, `strategies/alpha_baseline/README.md`
- **ADRs:** N/A
