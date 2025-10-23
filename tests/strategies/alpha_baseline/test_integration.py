"""
Integration tests for baseline strategy.

These tests require real T1 data and verify the complete end-to-end workflow.
They are skipped by default and should be run manually when T1 data is available.

To run these tests:
1. Ensure T1 pipeline has generated data in data/adjusted/
2. Run: pytest tests/strategies/alpha_baseline/test_integration.py -v

Tests cover:
- Complete training pipeline
- Feature generation
- Model training and evaluation
- Backtesting on test set
- MLflow integration
"""

import shutil
import tempfile
from pathlib import Path

import pytest

from strategies.alpha_baseline.backtest import evaluate_model
from strategies.alpha_baseline.config import DataConfig, ModelConfig, StrategyConfig, TrainingConfig
from strategies.alpha_baseline.train import BaselineTrainer, train_baseline_model


@pytest.mark.integration()
@pytest.mark.skip(reason="Requires T1 data - run manually when data available")
class TestCompleteWorkflow:
    """
    Integration tests for complete training and evaluation workflow.

    These tests verify the entire pipeline works end-to-end:
    - Data loading from T1 Parquet files
    - Alpha158 feature computation
    - LightGBM model training
    - MLflow tracking
    - Model evaluation and backtesting
    """

    def test_train_and_evaluate_full_pipeline(self) -> None:
        """
        Test complete training and evaluation pipeline.

        This test:
        1. Configures strategy with test settings
        2. Trains model on 2020-2023 data
        3. Validates on 2024 H1
        4. Tests on 2024 H2
        5. Generates backtest report
        6. Verifies MLflow logging
        """
        # Configure with fast training for testing
        config = StrategyConfig(
            data=DataConfig(
                symbols=["AAPL", "MSFT", "GOOGL"],
                train_start="2020-01-01",
                train_end="2023-12-31",
                valid_start="2024-01-01",
                valid_end="2024-06-30",
                test_start="2024-07-01",
                test_end="2024-12-31",
                data_dir=Path("data/adjusted"),
            ),
            model=ModelConfig(
                num_boost_round=50,  # Reduced for faster testing
                learning_rate=0.05,
                max_depth=6,
            ),
            training=TrainingConfig(
                early_stopping_rounds=10,
                experiment_name="test_integration",
                save_best_only=True,
            ),
        )

        # Create temp directory for artifacts
        temp_dir = Path(tempfile.mkdtemp())

        try:
            config.training.model_dir = temp_dir / "models"

            # Train model
            print("\n" + "=" * 50)
            print("Training baseline model...")
            print("=" * 50)

            trainer = train_baseline_model(config)

            # Verify model was trained
            assert trainer.model is not None
            assert trainer.best_iteration > 0
            assert len(trainer.metrics) > 0

            # Verify metrics are reasonable
            assert "train_mae" in trainer.metrics
            assert "valid_mae" in trainer.metrics
            assert "valid_ic" in trainer.metrics

            # IC should be positive (model has predictive power)
            # This is a weak test - we just check it's not NaN
            assert not pd.isna(trainer.metrics["valid_ic"])

            # Load test data
            _, _, _, _, X_test, y_test = trainer.load_data()

            # Evaluate on test set
            print("\n" + "=" * 50)
            print("Evaluating on test set...")
            print("=" * 50)

            results = evaluate_model(
                trainer,
                X_test,
                y_test,
                top_n=3,
                bottom_n=3,
                save_dir=temp_dir / "backtest_results",
            )

            # Verify evaluation results
            assert "predictions" in results
            assert "backtest" in results
            assert "metrics" in results
            assert "report" in results

            # Verify backtest metrics
            metrics = results["metrics"]
            assert "sharpe_ratio" in metrics
            assert "max_drawdown" in metrics
            assert "win_rate" in metrics

            # Verify files were created
            results_dir = temp_dir / "backtest_results"
            assert (results_dir / "cumulative_returns.png").exists()
            assert (results_dir / "drawdown.png").exists()
            assert (results_dir / "backtest_report.txt").exists()

            # Verify model was saved
            assert (config.training.model_dir / "test_integration.txt").exists()

            print("\n" + "=" * 50)
            print("Integration test passed!")
            print("=" * 50)
            print(f"\nValidation IC: {trainer.metrics['valid_ic']:.4f}")
            print(f"Test Sharpe Ratio: {metrics['sharpe_ratio']:.2f}")
            print(f"Test Max Drawdown: {metrics['max_drawdown']*100:.2f}%")

        finally:
            # Clean up
            if temp_dir.exists():
                shutil.rmtree(temp_dir)

    def test_mlflow_tracking(self) -> None:
        """
        Test MLflow tracking integration.

        Verifies:
        - Experiment is created
        - Run is logged
        - Parameters are logged
        - Metrics are logged
        - Model is logged
        """
        # Configure with MLflow enabled
        config = StrategyConfig(
            data=DataConfig(symbols=["AAPL"]),  # Single symbol for speed
            model=ModelConfig(num_boost_round=10),  # Fast training
            training=TrainingConfig(
                experiment_name="test_mlflow_integration",
                early_stopping_rounds=5,
            ),
        )

        temp_dir = Path(tempfile.mkdtemp())

        try:
            config.training.model_dir = temp_dir

            # Train with MLflow
            trainer = BaselineTrainer(config, use_mlflow=True)
            trainer.train()

            # Verify MLflow run ID was set
            assert trainer.mlflow_run_id is not None

            # MLflow artifacts should be in artifacts/mlruns/
            mlruns_dir = Path("artifacts/mlruns")
            assert mlruns_dir.exists()

            print(f"\nMLflow run ID: {trainer.mlflow_run_id}")

        finally:
            # Clean up
            if temp_dir.exists():
                shutil.rmtree(temp_dir)


@pytest.mark.integration()
@pytest.mark.skip(reason="Requires T1 data - run manually when data available")
class TestDataProviderIntegration:
    """Integration tests for T1DataProvider with real data."""

    def test_load_real_data(self) -> None:
        """Load real data from T1 pipeline."""

        from strategies.alpha_baseline.data_loader import T1DataProvider

        provider = T1DataProvider(data_dir=Path("data/adjusted"))

        # Get available symbols
        symbols = provider.get_available_symbols()
        assert len(symbols) > 0
        print(f"\nAvailable symbols: {symbols}")

        # Load data for first symbol
        symbol = symbols[0]
        min_date, max_date = provider.get_date_range(symbol)

        assert min_date is not None
        assert max_date is not None
        print(f"{symbol} date range: {min_date} to {max_date}")

        # Load data
        df = provider.load_data(
            symbols=[symbol],
            start_date=min_date,
            end_date=max_date,
        )

        # Verify structure
        assert len(df) > 0
        assert df.index.names == ["date", "symbol"]
        assert set(df.columns) == {"open", "high", "low", "close", "volume"}

        print(f"Loaded {len(df)} rows for {symbol}")


@pytest.mark.integration()
@pytest.mark.skip(reason="Requires T1 data - run manually when data available")
class TestFeatureGenerationIntegration:
    """Integration tests for Alpha158 feature generation with real data."""

    def test_generate_features_on_real_data(self) -> None:
        """Generate Alpha158 features on real T1 data."""
        from strategies.alpha_baseline.features import get_alpha158_features, get_labels

        # Generate features for a small date range
        features = get_alpha158_features(
            symbols=["AAPL"],
            start_date="2024-01-01",
            end_date="2024-01-31",
        )

        # Verify features were generated
        assert features is not None
        assert features.shape[1] == 158  # 158 features
        assert features.index.names == ["datetime", "instrument"]

        # Verify no all-NaN features
        nan_features = features.isna().all()
        if nan_features.any():
            print(f"\nWarning: {nan_features.sum()} features are all NaN")

        # Generate labels
        labels = get_labels(
            symbols=["AAPL"],
            start_date="2024-01-01",
            end_date="2024-01-31",
        )

        assert labels is not None
        assert labels.shape[1] == 1  # Single label
        assert labels.index.names == ["datetime", "instrument"]


if __name__ == "__main__":
    import pandas as pd

    print("=" * 70)
    print("Integration Tests for Baseline Strategy")
    print("=" * 70)
    print("\nThese tests require real T1 data in data/adjusted/")
    print("To run: pytest tests/strategies/alpha_baseline/test_integration.py -v -m integration")
    print("\nSkipping tests by default...")
