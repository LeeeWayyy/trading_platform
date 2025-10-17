# Baseline Strategy Tests

This directory contains comprehensive tests for the alpha baseline strategy.

## Test Structure

```
tests/strategies/alpha_baseline/
├── __init__.py
├── test_backtest.py          # Backtesting and evaluation (13 tests)
├── test_config.py             # Configuration management (13 tests)
├── test_data_loader.py        # T1 data provider (16 tests)
├── test_features.py           # Alpha158 features (7 tests)
├── test_train.py              # Training pipeline (13 tests)
├── test_integration.py        # End-to-end integration (3 test classes)
└── README.md                  # This file
```

## Test Summary

### Unit Tests (48 passing)

**test_data_loader.py** - 16 tests
- T1DataProvider initialization and configuration
- Data loading (single/multiple symbols)
- Date range filtering
- Field selection
- Empty result handling
- Sort order verification
- Column name standardization
- MultiIndex structure
- Helper methods (get_available_symbols, get_date_range)

**test_config.py** - 13 tests
- DataConfig (default/custom initialization)
- ModelConfig (initialization, to_dict conversion)
- TrainingConfig (initialization)
- StrategyConfig (composition, from_dict)
- DEFAULT_CONFIG validation

**test_features.py** - 2 passing, 5 skipped
- Module imports
- Function signatures
- Skipped: Qlib initialization, feature generation (require Qlib data format)

**test_train.py** - 5 passing, 8 skipped
- BaselineTrainer initialization (default/custom config)
- Error handling (predict/save without training)
- Module imports
- Skipped: Data loading, training, predictions, model persistence (require Qlib data)

**test_backtest.py** - 12 passing, 1 skipped
- PortfolioBacktest initialization
- Backtest execution
- Portfolio return computation
- Metrics calculation (Sharpe, drawdown, win rate, etc.)
- Plot generation (cumulative returns, drawdown)
- Report generation
- Error handling
- Edge cases (perfect/opposite predictions)
- Skipped: Full model evaluation (requires trained model)

### Integration Tests (3 test classes, all skipped)

**test_integration.py** - Requires T1 data
- Complete training and evaluation workflow
- MLflow tracking integration
- T1DataProvider with real data
- Alpha158 feature generation with real data

These tests are skipped by default and should be run manually when T1 data is available.

## Running Tests

### Run all unit tests:
```bash
pytest tests/strategies/alpha_baseline/ -v
```

### Run specific test file:
```bash
pytest tests/strategies/alpha_baseline/test_config.py -v
```

### Run integration tests (requires T1 data):
```bash
pytest tests/strategies/alpha_baseline/test_integration.py -v -m integration
```

### Run with coverage:
```bash
pytest tests/strategies/alpha_baseline/ --cov=strategies.alpha_baseline --cov-report=html
```

## Test Coverage

Current coverage (unit tests only):
- **test_data_loader.py**: 16/16 passing (100%)
- **test_config.py**: 13/13 passing (100%)
- **test_features.py**: 2/7 passing (5 skipped for integration)
- **test_train.py**: 5/13 passing (8 skipped for integration)
- **test_backtest.py**: 12/13 passing (1 skipped for integration)

**Total: 48 passing, 14 skipped**

Skipped tests require:
- Real T1 Parquet data in `data/adjusted/`
- Qlib data format conversion
- Full training pipeline execution (slow)

## Test Philosophy

### Unit Tests (Fast, No Dependencies)
- Test individual functions and classes in isolation
- Use mock data and temporary directories
- Should run in < 30 seconds total
- Can run in CI/CD pipeline

### Integration Tests (Slow, Real Data)
- Test complete workflows end-to-end
- Use real T1 data and Qlib
- May take several minutes
- Run manually or in nightly builds

## Adding New Tests

When adding new functionality:

1. **Write unit tests first** - Test the interface with mocks
2. **Add integration tests** - Test with real data if applicable
3. **Update this README** - Document new test coverage
4. **Mark slow tests** - Use `@pytest.mark.skip` for tests requiring real data

Example:
```python
@pytest.mark.skip(reason="Requires T1 data - integration test")
def test_with_real_data():
    # Test implementation
    pass
```

## Test Data

Unit tests use:
- Temporary directories (auto-cleaned)
- Mock Pandas/Polars DataFrames
- Small synthetic datasets (10 days, 5 symbols)

Integration tests require:
- Real T1 adjusted data: `data/adjusted/YYYY-MM-DD/*.parquet`
- MLflow tracking: `artifacts/mlruns/`
- Model artifacts: `artifacts/models/`

## Known Limitations

1. **Qlib Data Format** - Alpha158 features require Qlib's native data format, which is different from T1's Parquet format. Integration between T1 and Qlib needs a bridge (to be implemented).

2. **Mock vs Real Data** - Unit tests use synthetic data that may not reflect real market behavior. Integration tests are essential for validating real-world performance.

3. **No Transaction Costs** - Backtests assume zero transaction costs. This will be addressed in future iterations.

4. **Single Strategy** - Tests cover only the baseline strategy. Additional strategies will need their own test suites.

## Future Improvements

- [ ] Add performance benchmarks (training time, memory usage)
- [ ] Add property-based tests (hypothesis library)
- [ ] Add mutation testing (mutmut library)
- [ ] Increase integration test coverage
- [ ] Add continuous integration (GitHub Actions)
- [ ] Add test fixtures for common data patterns

## Questions or Issues?

- Review `/docs/IMPLEMENTATION_GUIDES/t2-baseline-strategy-qlib.md`
- Check `/docs/CONCEPTS/` for conceptual explanations
- File issues in GitHub issue tracker
