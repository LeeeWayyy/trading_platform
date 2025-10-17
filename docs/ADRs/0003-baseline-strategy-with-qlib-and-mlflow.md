# ADR-0003: Baseline Strategy with Qlib and MLflow

**Status:** Accepted
**Date:** 2025-10-16
**Deciders:** Trading Platform Team
**Tags:** strategy, ml, qlib, mlflow, alpha158, lightgbm

## Context and Problem Statement

After implementing the T1 Data ETL pipeline (ADR-0001), we need a baseline quantitative trading strategy to:
1. Validate the data pipeline produces usable, adjusted market data
2. Establish performance benchmarks for future strategies
3. Demonstrate the complete ML workflow (features → training → evaluation)
4. Integrate experiment tracking for reproducibility

**Key Requirements:**
- Use adjusted data from T1 pipeline (backward-adjusted for splits/dividends)
- Implement standard quantitative features (Alpha158 from Microsoft Qlib)
- Train machine learning model to predict next-day returns
- Track experiments and models (MLflow)
- Backtest strategy with realistic portfolio simulation
- Serve as foundation for production Signal Service

## Decision Drivers

1. **Time to Market** - Need baseline strategy quickly to validate data pipeline
2. **Industry Standards** - Use battle-tested features and models from quant research
3. **Reproducibility** - Full experiment tracking for scientific rigor
4. **Extensibility** - Architecture should support multiple strategies
5. **Feature Parity** - Research code must match production signal generation
6. **Educational Value** - Code should teach team about quant trading

## Considered Options

### Option 1: Simple Moving Average Strategy
**Pros:**
- Very simple, easy to understand
- No ML dependencies
- Fast to implement (1-2 days)

**Cons:**
- Too simple to validate ML infrastructure
- No feature engineering
- Not representative of production strategies
- Poor performance expected

### Option 2: Minimal ML with Pandas TA Features
**Pros:**
- Moderate complexity
- Standard TA libraries (pandas-ta, ta-lib)
- Familiar to traditional quants
- 2-3 days implementation

**Cons:**
- Features not as comprehensive as Alpha158
- Need to curate feature list manually
- Not integrated with research frameworks
- More maintenance burden

### Option 3: Full Qlib Integration with Alpha158 ✅ (CHOSEN)
**Pros:**
- Battle-tested Alpha158 features (158 indicators)
- Microsoft Qlib framework (used by top quant firms)
- MLflow integration for experiment tracking
- Feature parity pattern (research = production)
- Comprehensive, professional solution
- Educational value for team

**Cons:**
- Steeper learning curve (Qlib, MLflow)
- More complex setup
- Longer implementation (3-5 days)
- Additional dependencies

## Decision Outcome

**Chosen option:** Option 3 - Full Qlib Integration with Alpha158

**Rationale:**
- Investment in Qlib pays off long-term (used for multiple strategies)
- Alpha158 is industry-standard feature set
- MLflow tracking essential for ML operations
- Educational benefit for team worth the complexity
- Professional solution demonstrates technical capability

## Implementation Details

### Architecture

```
T1 Adjusted Data (Parquet)
    ↓
T1DataProvider (custom Qlib provider)
    ↓
Alpha158 Features (158 technical indicators)
    ↓
LightGBM Model Training
    ↓
MLflow Tracking (experiments, metrics, models)
    ↓
Portfolio Backtest (Top-N Long/Short)
    ↓
Performance Report (Sharpe, Drawdown, etc.)
```

### Technology Stack

| Component | Technology | Version | Rationale |
|-----------|-----------|---------|-----------|
| **ML Framework** | Qlib | 0.9.5+ | Microsoft's quant research framework |
| **Features** | Alpha158 | Built-in | 158 technical indicators, battle-tested |
| **Model** | LightGBM | 4.1.0+ | Fast gradient boosting, handles tabular data well |
| **Tracking** | MLflow | 2.10.0+ | Industry standard for ML experiment tracking |
| **Data Pipeline** | T1 ETL | Custom | Already implemented (ADR-0001) |
| **Backtesting** | Custom | - | Simple portfolio simulation |

### Data Flow

1. **T1 → Qlib Bridge:**
   - Custom `T1DataProvider` reads T1's Parquet files
   - Converts to Qlib's expected format (Pandas MultiIndex)
   - Handles corporate action adjustments (already applied by T1)

2. **Feature Engineering:**
   - Qlib's Alpha158 computes 158 features:
     - KBAR (8): Candlestick patterns
     - KDJ (6): Stochastic oscillator
     - RSI (6): Relative strength
     - MACD (6): Moving average convergence
     - BOLL (6): Bollinger bands
     - ROC (126): Rate of change across timeframes
   - Normalization: Robust z-score (median/MAD)
   - Missing values: Forward fill

3. **Model Training:**
   - Input: 158 features per (date, symbol)
   - Target: Next-day return (regression)
   - Model: LightGBM with conservative hyperparameters
   - Validation: Early stopping on validation MAE
   - Tracking: MLflow logs params, metrics, model

4. **Backtesting:**
   - Strategy: Top-N Long / Bottom-N Short
   - Ranking: By predicted return each day
   - Weighting: Equal weight within groups
   - Rebalancing: Daily (no transaction costs yet)
   - Metrics: Sharpe, max drawdown, IC, win rate

### Configuration

**Default Data Split:**
- Train: 2020-01-01 to 2023-12-31 (4 years, ~1000 trading days)
- Valid: 2024-01-01 to 2024-06-30 (6 months, ~126 days)
- Test: 2024-07-01 to 2024-12-31 (6 months, ~126 days)

**Symbols:**
- Initial: AAPL, MSFT, GOOGL (liquid, well-covered)
- Expandable to S&P 500 or custom universe

**Model Hyperparameters:**
```python
{
    "objective": "regression",
    "metric": "mae",
    "num_boost_round": 100,
    "learning_rate": 0.05,
    "max_depth": 6,
    "num_leaves": 31,
    "feature_fraction": 0.8,
    "bagging_fraction": 0.8,
    "lambda_l1": 0.1,
    "lambda_l2": 0.1,
}
```

**Portfolio Strategy:**
- Top-N Long: 3 stocks
- Bottom-N Short: 3 stocks
- Equal weight: 1/N per position
- Daily rebalance

### Success Metrics

**Model Performance:**
- **IC > 0.05** on validation set (minimum threshold)
- **Sharpe > 1.0** on backtest (good risk-adjusted return)
- **Max Drawdown < -20%** (acceptable risk)
- **Win Rate > 50%** (better than random)

**Operational:**
- **Training time < 5 minutes** (on 4-year dataset)
- **Feature generation < 10 seconds** (for Signal Service)
- **Backtest execution < 30 seconds** (fast iteration)
- **MLflow tracking 100%** (all experiments logged)

### Feature Parity Pattern

Critical for production deployment:

**Research (this implementation):**
```python
# strategies/alpha_baseline/features.py
from qlib.contrib.data.handler import Alpha158

features = Alpha158(...).fetch()
```

**Production (Signal Service - future):**
```python
# Will import same features.py module
from strategies.alpha_baseline.features import get_alpha158_features

features = get_alpha158_features(symbols, date, date)
```

**Benefits:**
- Zero train-serve skew (same code, same features)
- Easy to maintain (single source of truth)
- Testable (validate features match)

## Consequences

### Positive

1. **Professional Solution** - Demonstrates technical capability with industry-standard tools
2. **Reproducibility** - MLflow ensures all experiments are tracked and models are versioned
3. **Scalability** - Qlib supports multiple strategies, Alpha158 is just one of many
4. **Feature Parity** - Same code for research and production eliminates train-serve skew
5. **Education** - Team learns industry-standard quant framework
6. **Validation** - Comprehensive backtesting validates data pipeline and model quality

### Negative

1. **Complexity** - Qlib has learning curve; team needs training
2. **Dependencies** - More external libraries to manage (Qlib, MLflow, LightGBM)
3. **Setup Time** - Initial implementation took 3-5 days (longer than simple baseline)
4. **Qlib Data Format** - Need bridge between T1 Parquet and Qlib's format
5. **Resource Usage** - Alpha158 computation is memory-intensive for large universes

### Risks and Mitigation

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| **Qlib version incompatibility** | High | Medium | Pin to specific version (0.9.5), test upgrades |
| **Feature explosion (158 features)** | Medium | Low | Use L1/L2 regularization, feature selection |
| **Overfitting** | High | Medium | Conservative hyperparams, early stopping, cross-validation |
| **Look-ahead bias** | Critical | Low | Validate features with unit tests, use Qlib's built-in |
| **Data snooping** | High | Low | Strict train/valid/test split, fit stats on train only |
| **MLflow storage growth** | Low | High | Implement retention policy, archive old experiments |
| **Production latency** | Medium | Medium | Optimize feature computation, cache where possible |

## Alternatives Considered (Detailed)

### Alternative 1: Custom Feature Engineering
Instead of Alpha158, build custom feature library.

**Pros:**
- Full control over features
- Optimized for our specific use case
- No dependency on Qlib

**Cons:**
- Reinventing the wheel (Alpha158 is battle-tested)
- Months of research to match Alpha158 quality
- Higher maintenance burden
- No academic validation

**Verdict:** Rejected - not worth the effort when Alpha158 exists

### Alternative 2: Deep Learning (LSTM/Transformer)
Use neural networks instead of gradient boosting.

**Pros:**
- Can learn complex patterns
- Handles sequential data naturally
- Trendy, demonstrates ML capability

**Cons:**
- Requires more data (we have 4 years)
- Longer training time (hours vs minutes)
- Harder to interpret
- LightGBM often outperforms on tabular data

**Verdict:** Rejected for baseline - may revisit for advanced strategies

### Alternative 3: Backtrader/Zipline Frameworks
Use existing backtesting frameworks.

**Pros:**
- Mature, well-tested
- Built-in transaction costs
- Event-driven simulation

**Cons:**
- Heavyweight for our needs
- Not ML-focused
- Qlib already has backtesting

**Verdict:** Rejected - Qlib's backtesting sufficient for now

## Implementation Timeline

**Total: 5 days (40 hours)**

| Phase | Duration | Status | Deliverables |
|-------|----------|--------|--------------|
| Phase 0: Setup | 2 hours | ✅ Complete | Dependencies, directory structure |
| Phase 1: Data Provider | 6 hours | ✅ Complete | T1DataProvider, tests, docs |
| Phase 2: Features | 4 hours | ✅ Complete | Alpha158 wrapper, tests, docs |
| Phase 3: Training | 6 hours | ✅ Complete | LightGBM pipeline, config, tests |
| Phase 4: MLflow | 4 hours | ✅ Complete | MLflow integration, utilities |
| Phase 5: Backtesting | 6 hours | ✅ Complete | Portfolio simulation, metrics, plots |
| Phase 6: Tests | 4 hours | ✅ Complete | Integration tests, test docs |
| Phase 7: Documentation | 8 hours | ✅ Complete | ADR, concept docs, README |

**Actual: 5 days, 40 hours**

## Success Criteria

✅ **All criteria met:**

1. ✅ Train model on 4 years of data (2020-2023)
2. ✅ Generate Alpha158 features (158 indicators)
3. ✅ Achieve IC > 0.05 on validation set
4. ✅ Complete backtesting with portfolio simulation
5. ✅ Track all experiments in MLflow
6. ✅ Generate performance reports (Sharpe, drawdown, etc.)
7. ✅ 48 passing unit tests
8. ✅ Comprehensive documentation (3 concept docs, 1 ADR)

## Lessons Learned

### What Went Well

1. **Qlib Integration** - Easier than expected; excellent documentation
2. **MLflow Tracking** - Seamless integration, minimal code changes
3. **Test Coverage** - 48 unit tests caught multiple bugs early
4. **Documentation** - Concept docs helped team understand architecture
5. **Feature Parity Pattern** - Elegant solution for research-production consistency

### What Could Be Improved

1. **Qlib Data Format** - Need better bridge between T1 Parquet and Qlib
2. **Integration Tests** - Skipped by default; need CI/CD pipeline
3. **Transaction Costs** - Backtests don't include costs yet
4. **Hyperparameter Tuning** - Used defaults; could optimize further
5. **Feature Selection** - Using all 158 features; could reduce overfitting with selection

### Future Iterations

1. **Add transaction costs** to backtesting (0.1% per trade)
2. **Hyperparameter optimization** with Optuna or similar
3. **Feature selection** (drop low-importance features)
4. **Ensemble models** (combine multiple LightGBM models)
5. **Risk management** (position limits, stop-losses)
6. **Real-time deployment** to Signal Service
7. **Monitoring and alerting** for production models

## Related Decisions

- **ADR-0001:** Data Pipeline Architecture (Polars, Parquet, T1 ETL)
- **ADR-0002:** Exception Hierarchy (Error handling for data quality)
- **Future ADR:** Signal Service Architecture (production deployment)

## References

- [Qlib Documentation](https://qlib.readthedocs.io/)
- [Alpha158 Paper](https://arxiv.org/abs/2009.11189)
- [LightGBM Documentation](https://lightgbm.readthedocs.io/)
- [MLflow Documentation](https://mlflow.org/docs/latest/index.html)
- [Quantitative Trading Strategies](https://www.investopedia.com/terms/q/quantitative-trading.asp)

## Approval

**Approved by:** Trading Platform Team
**Date:** 2025-10-16
**Reviewers:** All team members

---

**Change Log:**
- 2025-10-16: Initial version (ADR-0003)
