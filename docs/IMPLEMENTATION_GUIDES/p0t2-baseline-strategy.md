# T2 Implementation Guide: Baseline Strategy with Qlib + MLflow

## Overview

This guide details the implementation of T2 (Baseline Strategy + MLflow), integrating Microsoft's Qlib quantitative investment platform with MLflow experiment tracking.

**Ticket:** T2 — Baseline Strategy + MLflow
**Output:** Trained model, metrics logged
**Status:** Planning Phase
**Branch:** `feature/t2-alpaca-connector`

## Goals

1. Integrate Qlib quantitative investment platform
2. Implement Alpha158 feature set for baseline strategy
3. Train LightGBM model on adjusted OHLCV data from T1
4. Integrate MLflow for experiment tracking
5. Generate backtesting results with performance metrics
6. Establish feature parity pattern for research/production

## Architecture Overview

```
T1 Adjusted Data (Parquet)
    ↓
Qlib Data Loader (Custom Provider)
    ↓
Alpha158 Feature Engineering
    ↓
LightGBM Model Training
    ↓
MLflow Experiment Tracking (metrics, params, artifacts)
    ↓
Model Registry (artifacts/models/)
    ↓
Backtest Evaluation
```

## Phase 0: Prerequisites & Setup

### Dependencies to Add

```toml
# pyproject.toml additions
[tool.poetry.dependencies]
pyqlib = "^0.9.5"           # Qlib platform
mlflow = "^2.10.0"          # Experiment tracking
lightgbm = "^4.1.0"         # Gradient boosting
matplotlib = "^3.8.0"       # Visualization
seaborn = "^0.13.0"         # Statistical plots
scikit-learn = "^1.4.0"     # ML utilities
```

### Environment Variables

```bash
# .env additions
QLIB_DATA_DIR=./data/qlib_data
MLFLOW_TRACKING_URI=file:./artifacts/mlruns
MLFLOW_EXPERIMENT_NAME=alpha_baseline
```

### Directory Structure

```
strategies/
└── alpha_baseline/
    ├── __init__.py
    ├── config.py           # Strategy configuration
    ├── data_loader.py      # Custom Qlib data provider
    ├── features.py         # Feature engineering (shared)
    ├── model.py            # Model definition
    ├── train.py            # Training pipeline
    ├── backtest.py         # Backtesting logic
    └── evaluate.py         # Performance metrics

artifacts/
├── mlruns/                 # MLflow tracking data
└── models/
    └── alpha_baseline/     # Saved models
```

## Phase 1: Custom Qlib Data Provider

### Challenge: Integrating T1 Output with Qlib

Qlib expects data in its own format, but we have T1 adjusted Parquet files. We need a custom data provider.

### Implementation: `data_loader.py`

```python
"""
Custom Qlib data provider that reads T1 adjusted Parquet data.

This bridges T1's output with Qlib's expected data format, allowing
us to use adjusted OHLCV data for feature engineering and modeling.
"""

import polars as pl
import pandas as pd
from pathlib import Path
from typing import List, Optional
from datetime import date

from qlib.data.dataset import DatasetH
from qlib.data.dataset.handler import DataHandlerLP


class T1DataProvider:
    """
    Loads adjusted data from T1 pipeline output.

    Converts Polars Parquet → Pandas DataFrame in Qlib's expected format.
    """

    def __init__(self, data_dir: Path = Path("data/adjusted")):
        self.data_dir = data_dir

    def load_data(
        self,
        symbols: List[str],
        start_date: date,
        end_date: date,
        fields: List[str] = ["open", "high", "low", "close", "volume"]
    ) -> pd.DataFrame:
        """
        Load adjusted data for given symbols and date range.

        Returns:
            DataFrame with MultiIndex (date, symbol) and OHLCV columns
        """
        # Implementation details...
```

### Key Considerations

- **Data Format**: Qlib expects `pd.DataFrame` with `(date, symbol)` MultiIndex
- **Column Naming**: Qlib uses lowercase (`open`, `close` not `Open`, `Close`)
- **Corporate Actions**: Already handled by T1 pipeline
- **Missing Data**: Qlib handles this, but we should validate continuity

## Phase 2: Alpha158 Feature Engineering

### Alpha158 Overview

Alpha158 is a comprehensive feature set with 158 features across 6 categories:

1. **KBAR** (20 features): OHLCV-based features
   - Returns, volatility, volume ratios
2. **KDJ** (12 features): Stochastic oscillator variants
3. **RSI** (12 features): Relative Strength Index variants
4. **MACD** (20 features): Moving Average Convergence Divergence
5. **BOLL** (18 features): Bollinger Bands
6. **ROC** (76 features): Rate of Change variants

### Feature Parity Strategy

**Key Principle:** Features must be computed identically in research and production.

```python
# strategies/alpha_baseline/features.py

from qlib.contrib.data.handler import Alpha158


class BaselineFeatures(Alpha158):
    """
    Baseline feature set based on Alpha158.

    This class is used by BOTH:
    - Offline research/training (this module)
    - Online Signal Service (T3 will import this)

    NEVER duplicate feature computation logic.
    """

    def __init__(self, instruments="csi300", **kwargs):
        super().__init__(instruments=instruments, **kwargs)

    @classmethod
    def compute_features(cls, df: pd.DataFrame) -> pd.DataFrame:
        """
        Compute Alpha158 features from OHLCV data.

        Args:
            df: DataFrame with (date, symbol) MultiIndex and OHLCV columns

        Returns:
            DataFrame with 158 feature columns added
        """
        # Qlib handles feature computation internally
        # This method is for future custom features
        pass
```

## Phase 3: Model Training Pipeline

### Training Configuration

```python
# strategies/alpha_baseline/config.py

from pydantic import BaseModel
from datetime import date


class TrainingConfig(BaseModel):
    """Hyperparameters for baseline strategy training."""

    # Data split
    train_start: date = date(2020, 1, 1)
    train_end: date = date(2023, 12, 31)
    valid_start: date = date(2024, 1, 1)
    valid_end: date = date(2024, 6, 30)
    test_start: date = date(2024, 7, 1)
    test_end: date = date(2024, 12, 31)

    # Model hyperparameters
    model_type: str = "LightGBM"
    learning_rate: float = 0.05
    num_leaves: int = 31
    max_depth: int = -1
    n_estimators: int = 100

    # Feature engineering
    feature_set: str = "Alpha158"

    # MLflow
    experiment_name: str = "alpha_baseline"
    run_name: str = "lightgbm_alpha158_v1"

    # Symbols
    symbols: list[str] = ["AAPL", "MSFT", "GOOGL"]
```

### Training Script

```python
# strategies/alpha_baseline/train.py

import qlib
import mlflow
from qlib.contrib.model.gbdt import LGBModel
from qlib.contrib.strategy import TopkDropoutStrategy
from qlib.contrib.evaluate import backtest
from qlib.workflow import R

from .config import TrainingConfig
from .data_loader import T1DataProvider


def train_baseline_model(config: TrainingConfig):
    """
    Train baseline strategy model with MLflow tracking.

    Workflow:
    1. Initialize Qlib with MLflow integration
    2. Load T1 adjusted data
    3. Compute Alpha158 features
    4. Train LightGBM model
    5. Log metrics, params, and artifacts to MLflow
    6. Run backtest and evaluate
    """

    # Initialize Qlib with MLflow
    qlib.init(
        provider_uri="~/.qlib/qlib_data/cn_data",
        exp_manager={
            "class": "MLflowExpManager",
            "module_path": "qlib.workflow.expm",
            "kwargs": {
                "uri": "file:./artifacts/mlruns",
                "default_exp_name": config.experiment_name,
            },
        },
    )

    # Start MLflow run
    with R.start(experiment_name=config.experiment_name, recorder_name=config.run_name):
        # Log hyperparameters
        R.log_params(**config.model_dump())

        # Load data through T1 provider
        data_handler = T1DataProvider()
        # ... training logic

        # Log metrics
        R.log_metrics(
            sharpe_ratio=sharpe,
            annual_return=annual_return,
            max_drawdown=max_dd,
            ic=ic,
            rank_ic=rank_ic,
        )

        # Save model artifact
        R.save_objects(**{"model.pkl": model})


if __name__ == "__main__":
    config = TrainingConfig()
    train_baseline_model(config)
```

## Phase 4: MLflow Integration

### Experiment Tracking Strategy

**What to Log:**

1. **Parameters** (hyperparameters)
   - Model type, learning rate, num_leaves, etc.
   - Feature set configuration
   - Train/valid/test date ranges
   - Symbols traded

2. **Metrics** (performance indicators)
   - **Sharpe Ratio**: Risk-adjusted returns
   - **Annual Return**: Total returns annualized
   - **Max Drawdown**: Worst peak-to-trough decline
   - **IC (Information Coefficient)**: Prediction accuracy
   - **Rank IC**: Rank correlation between predictions and returns
   - **Win Rate**: Percentage of profitable trades
   - **Turnover**: Trading frequency

3. **Artifacts** (files)
   - Trained model pickle file
   - Feature importance plot
   - Backtest equity curve
   - Predictions CSV
   - Backtest report JSON

### MLflow UI Access

```bash
# Start MLflow UI
mlflow ui --backend-store-uri file:./artifacts/mlruns

# Access at http://localhost:5000
```

### Model Registry Pattern

```python
# Register best model to production
import mlflow

client = mlflow.tracking.MlflowClient()
model_uri = f"runs:/{run_id}/model"

# Register model
client.create_registered_model("alpha_baseline")
client.create_model_version(
    name="alpha_baseline",
    source=model_uri,
    run_id=run_id,
)

# Promote to production
client.transition_model_version_stage(
    name="alpha_baseline",
    version=1,
    stage="Production",
)
```

## Phase 5: Backtesting & Evaluation

### Backtest Configuration

```python
# Backtest strategy: TopK long-only
strategy_config = {
    "topk": 3,           # Hold top 3 stocks
    "n_drop": 1,         # Drop bottom 1 each rebalance
    "signal": "pred",    # Use model predictions
}

# Backtest parameters
backtest_config = {
    "start_time": config.test_start,
    "end_time": config.test_end,
    "account": 100000,           # Initial capital
    "benchmark": "^GSPC",        # S&P 500 benchmark
    "exchange_kwargs": {
        "freq": "day",
        "limit_threshold": 0.095,  # 9.5% price limit
        "deal_price": "close",
        "open_cost": 0.0005,       # 5 bps trading cost
        "close_cost": 0.0015,
        "min_cost": 5,
    },
}
```

### Performance Metrics

```python
# Calculate key metrics
def calculate_metrics(backtest_result):
    """
    Calculate comprehensive performance metrics.

    Returns:
        dict with Sharpe, returns, drawdown, IC, etc.
    """
    returns = backtest_result["return"]

    metrics = {
        "sharpe_ratio": returns.mean() / returns.std() * np.sqrt(252),
        "annual_return": returns.mean() * 252,
        "annual_volatility": returns.std() * np.sqrt(252),
        "max_drawdown": calculate_max_drawdown(returns),
        "ic": calculate_ic(predictions, actual_returns),
        "rank_ic": calculate_rank_ic(predictions, actual_returns),
        "win_rate": (returns > 0).mean(),
        "profit_factor": returns[returns > 0].sum() / abs(returns[returns < 0].sum()),
    }

    return metrics
```

## Phase 6: Testing Strategy

### Test Structure

```
tests/
└── strategies/
    └── alpha_baseline/
        ├── test_data_loader.py      # T1 data provider tests
        ├── test_features.py         # Feature computation tests
        ├── test_model.py            # Model training tests
        ├── test_backtest.py         # Backtest validation tests
        └── test_integration.py      # End-to-end tests
```

### Key Test Cases

1. **Data Provider Tests**
   - Load adjusted data from T1
   - Handle missing dates
   - Validate MultiIndex format
   - Check column names

2. **Feature Tests**
   - Alpha158 computation correctness
   - Feature parity (research vs production)
   - Handle edge cases (IPOs, delistings)

3. **Model Tests**
   - Training completes successfully
   - Model can predict on new data
   - Predictions within valid range

4. **Backtest Tests**
   - Backtest runs without errors
   - Metrics calculated correctly
   - Position constraints respected

5. **Integration Tests**
   - Full pipeline: data → features → train → backtest
   - MLflow logging works
   - Artifacts saved correctly

## Phase 7: Documentation & ADR

### ADR Topics

**ADR-0003: Qlib Integration for Baseline Strategy**

Key decisions:
- Why Qlib over custom implementation?
- Alpha158 vs custom feature set
- LightGBM vs other models
- MLflow for experiment tracking
- Custom data provider vs Qlib's native formats

### Concept Documentation

**`/docs/CONCEPTS/alpha158-features.md`**
- Explanation of each feature category
- Financial intuition behind features
- When to use Alpha158 vs custom features

**`/docs/CONCEPTS/backtesting.md`**
- Backtest realism and assumptions
- Transaction costs and slippage
- Survivorship bias handling

## Implementation Checklist

### Phase 0: Setup
- [ ] Add Qlib, MLflow, LightGBM to dependencies
- [ ] Update requirements.txt and pyproject.toml
- [ ] Create directory structure
- [ ] Set up environment variables

### Phase 1: Data Integration
- [ ] Implement T1DataProvider
- [ ] Test data loading from adjusted Parquet
- [ ] Validate Qlib format compatibility
- [ ] Handle missing data edge cases

### Phase 2: Feature Engineering
- [ ] Implement BaselineFeatures class
- [ ] Validate Alpha158 computation
- [ ] Test feature parity pattern
- [ ] Document feature engineering logic

### Phase 3: Model Training
- [ ] Implement training configuration
- [ ] Create train.py with LightGBM
- [ ] Integrate MLflow tracking
- [ ] Test training pipeline

### Phase 4: MLflow Integration
- [ ] Configure MLflow tracking URI
- [ ] Implement parameter logging
- [ ] Implement metric logging
- [ ] Implement artifact storage
- [ ] Test MLflow UI access

### Phase 5: Backtesting
- [ ] Implement backtest configuration
- [ ] Run backtest on test data
- [ ] Calculate performance metrics
- [ ] Generate equity curve visualization

### Phase 6: Testing
- [ ] Write unit tests for all modules
- [ ] Write integration tests
- [ ] Achieve >90% code coverage
- [ ] Validate results match expectations

### Phase 7: Documentation
- [ ] Write ADR-0003
- [ ] Document Alpha158 features concept
- [ ] Document backtesting concepts
- [ ] Update REPO_MAP.md

## Expected Outputs

After T2 completion:

1. **Trained Model**
   - `artifacts/models/alpha_baseline/model.pkl`
   - LightGBM model trained on Alpha158 features

2. **MLflow Experiment**
   - `artifacts/mlruns/` with tracked runs
   - Metrics: Sharpe, IC, returns, drawdown
   - Artifacts: model, plots, predictions

3. **Backtest Results**
   - Performance report JSON
   - Equity curve plot
   - Position history CSV

4. **Documentation**
   - ADR-0003 explaining architecture choices
   - Alpha158 concept documentation
   - Updated implementation guide

5. **Tests**
   - 20+ tests covering all components
   - Integration test for full pipeline
   - >90% code coverage

## Success Criteria

✅ Model trains successfully on T1 adjusted data
✅ MLflow tracks experiments with metrics and artifacts
✅ Backtest produces realistic results (Sharpe > 0.5)
✅ Feature parity established (shared features.py)
✅ All tests pass (>90% coverage)
✅ Documentation complete (ADR + concepts)
✅ Ready for T3 (Signal Service can import features)

## Timeline Estimate

- Phase 0 (Setup): 2-3 hours
- Phase 1 (Data Integration): 4-6 hours
- Phase 2 (Features): 2-3 hours
- Phase 3 (Training): 4-6 hours
- Phase 4 (MLflow): 2-3 hours
- Phase 5 (Backtesting): 3-4 hours
- Phase 6 (Testing): 4-6 hours
- Phase 7 (Documentation): 2-3 hours

**Total: 23-34 hours (3-5 days with regular commits)**

## Next Steps

1. Review and approve this implementation plan
2. Create ADR-0003 (Qlib integration architecture)
3. Start Phase 0: dependency installation
4. Implement incrementally with regular commits
5. Create PR when all phases complete

---

**Note:** This is a living document. Update as implementation progresses.
