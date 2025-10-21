# Alpha Baseline Strategy

Production-ready baseline quantitative trading strategy using Microsoft Qlib framework with Alpha158 features and LightGBM model.

## Overview

This strategy serves as the foundation for the trading platform, demonstrating:
- ✅ Complete ML workflow (data → features → training → evaluation)
- ✅ Industry-standard feature engineering (Alpha158)
- ✅ Experiment tracking and model versioning (MLflow)
- ✅ Portfolio simulation and performance evaluation
- ✅ Feature parity between research and production

**Performance Targets:**
- IC > 0.05 on validation set
- Sharpe Ratio > 1.0 on backtest
- Max Drawdown < -20%

## Quick Start

### 1. Install Dependencies

```bash
# Using Poetry (recommended)
poetry install

# Or using pip
pip install -r requirements.txt

# Install libomp for LightGBM (macOS)
brew install libomp
```

### 2. Prepare Data

Ensure T1 pipeline has generated adjusted data:
```
data/adjusted/
├── 2024-01-01/
│   ├── AAPL.parquet
│   ├── MSFT.parquet
│   └── GOOGL.parquet
└── ...
```

### 3. Train Model

```bash
# Train with default configuration
python strategies/alpha_baseline/train.py

# Or use Python API
from strategies.alpha_baseline.train import train_baseline_model
from strategies.alpha_baseline.config import StrategyConfig

config = StrategyConfig()
trainer = train_baseline_model(config)
```

### 4. Evaluate Performance

```bash
# Evaluate on test set
from strategies.alpha_baseline.backtest import evaluate_model

results = evaluate_model(
    trainer,
    X_test,
    y_test,
    save_dir="artifacts/backtest_results"
)

print(results['report'])
```

### 5. View Results

- **MLflow UI:** `mlflow ui --port 5000` → http://localhost:5000
- **Plots:** Check `artifacts/backtest_results/`
- **Models:** Saved in `artifacts/models/`

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    T1 Adjusted Data                         │
│                      (Parquet Files)                         │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│              T1DataProvider (data_loader.py)                │
│  • Reads Parquet files                                      │
│  • Converts to Pandas MultiIndex                            │
│  • Filters by symbols and date range                        │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│           Alpha158 Features (features.py)                   │
│  • KBAR (8): Candlestick patterns                           │
│  • KDJ (6): Stochastic oscillator                           │
│  • RSI (6): Relative strength                               │
│  • MACD (6): Moving average convergence                     │
│  • BOLL (6): Bollinger bands                                │
│  • ROC (126): Rate of change (multi-timeframe)             │
│  = 158 features total                                       │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│         LightGBM Model Training (train.py)                  │
│  • Objective: Regression (predict next-day return)          │
│  • Early stopping on validation MAE                         │
│  • Hyperparameters: config.py                               │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ├─────────────────────┐
                         │                     │
                         ▼                     ▼
            ┌────────────────────┐  ┌─────────────────────┐
            │  MLflow Tracking   │  │  Model Persistence  │
            │  (mlflow_utils.py) │  │  (LightGBM .txt)    │
            │  • Experiments     │  │  • Best model saved │
            │  • Parameters      │  │  • Loadable later   │
            │  • Metrics         │  └─────────────────────┘
            │  • Models          │
            └────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│       Portfolio Backtesting (backtest.py)                   │
│  • Strategy: Top-N Long / Bottom-N Short                    │
│  • Ranking: By predicted return                             │
│  • Weighting: Equal weight (1/N per position)              │
│  • Rebalancing: Daily                                       │
│  • Metrics: Sharpe, Drawdown, IC, Win Rate                 │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
            ┌────────────────────────────┐
            │   Performance Reports       │
            │  • Cumulative returns plot  │
            │  • Drawdown plot            │
            │  • Metric table             │
            └─────────────────────────────┘
```

## Module Reference

### Core Modules

| Module | Purpose | Key Classes/Functions |
|--------|---------|----------------------|
| `config.py` | Configuration management | `StrategyConfig`, `DataConfig`, `ModelConfig` |
| `data_loader.py` | Data loading | `T1DataProvider` |
| `features.py` | Feature engineering | `get_alpha158_features()`, `get_labels()` |
| `train.py` | Model training | `BaselineTrainer`, `train_baseline_model()` |
| `backtest.py` | Backtesting | `PortfolioBacktest`, `evaluate_model()` |
| `mlflow_utils.py` | Experiment tracking | `initialize_mlflow()`, `log_config()` |

### Configuration (`config.py`)

```python
from strategies.alpha_baseline.config import StrategyConfig

# Default configuration
config = StrategyConfig()

# Custom configuration
config = StrategyConfig(
    data=DataConfig(
        symbols=["AAPL", "MSFT"],
        train_start="2020-01-01",
        train_end="2023-12-31",
    ),
    model=ModelConfig(
        learning_rate=0.1,
        max_depth=8,
    ),
    training=TrainingConfig(
        experiment_name="my_experiment",
    ),
)
```

### Data Loading (`data_loader.py`)

```python
from strategies.alpha_baseline.data_loader import T1DataProvider
from datetime import date

provider = T1DataProvider(data_dir="data/adjusted")

# Load data
df = provider.load_data(
    symbols=["AAPL", "MSFT"],
    start_date=date(2024, 1, 1),
    end_date=date(2024, 12, 31),
    fields=["close", "volume"],  # Optional
)

# Get available symbols
symbols = provider.get_available_symbols()

# Get date range for symbol
min_date, max_date = provider.get_date_range("AAPL")
```

### Feature Engineering (`features.py`)

```python
from strategies.alpha_baseline.features import (
    get_alpha158_features,
    get_labels,
    compute_features_and_labels,
)

# Get features
features = get_alpha158_features(
    symbols=["AAPL", "MSFT"],
    start_date="2024-01-01",
    end_date="2024-12-31",
    fit_start_date="2020-01-01",  # For normalization
    fit_end_date="2023-12-31",
)

# Get labels
labels = get_labels(
    symbols=["AAPL", "MSFT"],
    start_date="2024-01-01",
    end_date="2024-12-31",
)

# Get train/valid/test splits
X_train, y_train, X_valid, y_valid, X_test, y_test = compute_features_and_labels(
    symbols=["AAPL", "MSFT", "GOOGL"],
    train_start="2020-01-01",
    train_end="2023-12-31",
    valid_start="2024-01-01",
    valid_end="2024-06-30",
    test_start="2024-07-01",
    test_end="2024-12-31",
)
```

### Training (`train.py`)

```python
from strategies.alpha_baseline.train import BaselineTrainer, train_baseline_model

# Simple training
trainer = train_baseline_model()

# Advanced training
trainer = BaselineTrainer(config, use_mlflow=True)
trainer.train()

# Make predictions
predictions = trainer.predict(X_test)

# Save/load model
trainer.save_model(Path("my_model.txt"))
trainer.load_model(Path("my_model.txt"))
```

### Backtesting (`backtest.py`)

```python
from strategies.alpha_baseline.backtest import PortfolioBacktest, evaluate_model

# Manual backtesting
backtest = PortfolioBacktest(
    predictions=predictions,
    actual_returns=actual_returns,
    top_n=3,
    bottom_n=3,
)
metrics = backtest.run()

# Generate plots
backtest.plot_cumulative_returns(save_path="returns.png")
backtest.plot_drawdown(save_path="drawdown.png")

# Generate report
print(backtest.generate_report())

# Automated evaluation
results = evaluate_model(
    trainer,
    X_test,
    y_test,
    save_dir="results/",
)
```

### MLflow (`mlflow_utils.py`)

```python
from strategies.alpha_baseline.mlflow_utils import *

# Initialize MLflow
exp_id = initialize_mlflow(
    tracking_uri="file:./artifacts/mlruns",
    experiment_name="alpha_baseline",
)

# Log experiment
with get_or_create_run(exp_id, run_name="experiment_1"):
    log_config(config)
    log_metrics({"mae": 0.015, "ic": 0.052})
    log_model(model)

# Find best run
best_run = get_best_run("alpha_baseline", "valid_ic", ascending=False)

# Load model from run
model = load_model_from_run(best_run.info.run_id)
```

## Testing

### Run All Tests

```bash
# Unit tests (fast, no real data required)
pytest tests/strategies/alpha_baseline/ -v

# Coverage report
pytest tests/strategies/alpha_baseline/ --cov=strategies.alpha_baseline --cov-report=html
```

### Run Integration Tests

```bash
# Requires T1 data in data/adjusted/
pytest tests/strategies/alpha_baseline/test_integration.py -v -m integration
```

### Test Summary

- **48 passing unit tests** - Fast, mock data
- **14 skipped integration tests** - Require real data
- **< 3 seconds execution time** - Unit tests only

See `tests/strategies/alpha_baseline/README.md` for details.

## Documentation

### Concept Docs (Educational)

- **qlib-data-providers.md** - How Qlib data providers work
- **alpha158-features.md** - Alpha158 feature set explained
- **lightgbm-training.md** - LightGBM for stock prediction

### Implementation Guides (How-To)

- **[/docs/TASKS/P0T2_DONE.md](../../docs/TASKS/P0T2_DONE.md)** - Complete T2 implementation guide

### Architecture Decision Records (Why)

- **ADR-0003** - Baseline Strategy with Qlib and MLflow

## Performance Expectations

### Training Performance
- **Data:** 3 symbols × 1000 days = ~3000 samples
- **Features:** 158 features per sample
- **Training time:** 2-5 minutes
- **Memory:** ~500MB

### Backtest Performance
- **Test period:** 6 months (126 days)
- **Execution time:** < 30 seconds
- **Expected metrics:**
  - IC: 0.03-0.08 (weak to moderate signal)
  - Sharpe: 0.8-1.5 (decent risk-adjusted return)
  - Max Drawdown: -15% to -25%
  - Win Rate: 50-55%

## Known Limitations

1. **No Transaction Costs** - Backtests assume zero slippage/commission
2. **No Position Limits** - Can theoretically short unlimited shares
3. **No Risk Management** - No stop-losses or position sizing
4. **Daily Rebalancing** - Assumes can trade at close every day
5. **Equal Weighting** - Doesn't account for volatility differences
6. **Small Universe** - Only 3 symbols (not diversified)

## Future Improvements

### Short-term (Next Sprint)
- [ ] Add transaction costs to backtesting
- [ ] Implement position limits
- [ ] Add more symbols (S&P 500 constituents)
- [ ] Hyperparameter optimization (Optuna)

### Medium-term (Next Quarter)
- [ ] Feature selection (reduce from 158)
- [ ] Ensemble models (multiple LightGBM)
- [ ] Risk management (stop-losses, position sizing)
- [ ] Real-time deployment to Signal Service

### Long-term (Future)
- [ ] Alternative models (XGBoost, CatBoost, Neural Networks)
- [ ] Custom features (sentiment, fundamentals)
- [ ] Portfolio optimization (mean-variance, risk parity)
- [ ] Multi-timeframe strategies (daily + intraday)

## Troubleshooting

### Issue: "Qlib data format error"
**Solution:** Qlib expects specific data format. Use `T1DataProvider` which handles conversion.

### Issue: "MLflow tracking URI not found"
**Solution:** Ensure `artifacts/mlruns/` directory exists or run `initialize_mlflow()` first.

### Issue: "LightGBM libomp.dylib not found" (macOS)
**Solution:** Install libomp with `brew install libomp`

### Issue: "Out of memory during feature generation"
**Solution:** Reduce number of symbols or date range. Alpha158 is memory-intensive.

### Issue: "Model overfitting (train_mae << valid_mae)"
**Solution:** Increase regularization (`lambda_l1`, `lambda_l2`), reduce `max_depth`, or use more data.

## Contributing

When adding new functionality:

1. **Follow existing patterns** - See `config.py` for configuration style
2. **Write tests** - Unit tests required, integration tests encouraged
3. **Document thoroughly** - Docstrings + concept docs for complex features
4. **Update README** - Keep this file current

## Questions or Issues?

- **Documentation:** Check `/docs/CONCEPTS/` and `/docs/IMPLEMENTATION_GUIDES/`
- **ADRs:** See `/docs/ADRs/0003-baseline-strategy-with-qlib-and-mlflow.md`
- **Tests:** See `tests/strategies/alpha_baseline/README.md`
- **Issues:** File in GitHub issue tracker

---

**Version:** 1.0.0
**Last Updated:** 2025-10-16
**Maintainers:** Trading Platform Team
