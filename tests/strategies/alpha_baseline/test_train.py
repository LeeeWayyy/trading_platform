"""
Unit tests for model training pipeline.

Tests cover:
- BaselineTrainer initialization
- Configuration handling
- Model training workflow (integration test in Phase 6)
- Model persistence

Note: Full training tests require Qlib data format.
These tests verify structure and interfaces.
"""

import shutil
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from strategies.alpha_baseline.config import DataConfig, ModelConfig, StrategyConfig, TrainingConfig
from strategies.alpha_baseline.train import BaselineTrainer, train_baseline_model


class TestBaselineTrainer:
    """Tests for BaselineTrainer class."""

    def setup_method(self) -> None:
        """Create temporary directories for testing."""
        self.temp_dir = Path(tempfile.mkdtemp())
        self.model_dir = self.temp_dir / "models"
        self.model_dir.mkdir(parents=True)

    def teardown_method(self) -> None:
        """Clean up temporary directories."""
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def test_initialization_with_default_config(self) -> None:
        """Initialize trainer with default config."""
        trainer = BaselineTrainer()

        assert trainer.config is not None
        assert isinstance(trainer.config, StrategyConfig)
        assert trainer.model is None
        assert trainer.best_iteration == 0
        assert trainer.metrics == {}

    def test_initialization_with_custom_config(self) -> None:
        """Initialize trainer with custom config."""
        config = StrategyConfig(
            data=DataConfig(symbols=["AAPL"]),
            model=ModelConfig(learning_rate=0.1),
            training=TrainingConfig(early_stopping_rounds=10),
        )

        trainer = BaselineTrainer(config)

        assert trainer.config.data.symbols == ["AAPL"]
        assert trainer.config.model.learning_rate == 0.1
        assert trainer.config.training.early_stopping_rounds == 10

    @pytest.mark.skip(reason="Requires Qlib data format - integration test for Phase 6")
    def test_load_data(self) -> None:
        """Load data using config."""
        config = StrategyConfig(
            data=DataConfig(
                symbols=["AAPL", "MSFT"],
                train_start="2023-01-01",
                train_end="2023-12-31",
                valid_start="2024-01-01",
                valid_end="2024-06-30",
                test_start="2024-07-01",
                test_end="2024-12-31",
            )
        )

        trainer = BaselineTrainer(config)
        X_train, y_train, X_valid, y_valid, X_test, y_test = trainer.load_data()

        # Check shapes
        assert X_train.shape[1] == 158  # 158 features
        assert y_train.shape[1] == 1  # Single label
        assert X_train.shape[0] == y_train.shape[0]  # Same rows

        assert X_valid.shape[1] == 158
        assert X_valid.shape[0] == y_valid.shape[0]

        assert X_test.shape[1] == 158
        assert X_test.shape[0] == y_test.shape[0]

    @pytest.mark.skip(reason="Requires Qlib data format - integration test for Phase 6")
    def test_train_with_auto_data_loading(self) -> None:
        """Train model with automatic data loading."""
        config = StrategyConfig(
            model=ModelConfig(num_boost_round=10),  # Fast training for test
            training=TrainingConfig(
                early_stopping_rounds=5,
                model_dir=self.model_dir,
            ),
        )

        trainer = BaselineTrainer(config)
        model = trainer.train()

        # Check model was trained
        assert model is not None
        assert trainer.model is not None
        assert trainer.best_iteration > 0

        # Check metrics were computed
        assert "train_mae" in trainer.metrics
        assert "valid_mae" in trainer.metrics
        assert "train_ic" in trainer.metrics
        assert "valid_ic" in trainer.metrics

    @pytest.mark.skip(reason="Requires Qlib data format - integration test for Phase 6")
    def test_train_with_provided_data(self) -> None:
        """Train model with explicitly provided data."""
        # Create mock data
        np.random.seed(42)
        X_train = pd.DataFrame(np.random.randn(100, 158))
        y_train = pd.DataFrame(np.random.randn(100, 1))
        X_valid = pd.DataFrame(np.random.randn(50, 158))
        y_valid = pd.DataFrame(np.random.randn(50, 1))

        config = StrategyConfig(
            model=ModelConfig(num_boost_round=10),
            training=TrainingConfig(model_dir=self.model_dir),
        )

        trainer = BaselineTrainer(config)
        model = trainer.train(X_train, y_train, X_valid, y_valid)

        assert model is not None
        assert trainer.model is not None

    @pytest.mark.skip(reason="Requires trained model - integration test for Phase 6")
    def test_predict(self) -> None:
        """Make predictions with trained model."""
        # Train model first
        trainer = BaselineTrainer()
        trainer.train()

        # Create test data
        X_test = pd.DataFrame(np.random.randn(50, 158))

        # Make predictions
        predictions = trainer.predict(X_test)

        assert isinstance(predictions, np.ndarray)
        assert predictions.shape == (50,)

    def test_predict_without_training_raises_error(self) -> None:
        """Predict without training raises ValueError."""
        trainer = BaselineTrainer()
        X_test = pd.DataFrame(np.random.randn(50, 158))

        with pytest.raises(ValueError, match="not trained yet"):
            trainer.predict(X_test)

    @pytest.mark.skip(reason="Requires trained model - integration test for Phase 6")
    def test_save_model(self) -> None:
        """Save trained model to disk."""
        config = StrategyConfig(
            training=TrainingConfig(
                model_dir=self.model_dir,
                experiment_name="test_experiment",
            )
        )

        trainer = BaselineTrainer(config)
        trainer.train()

        # Save model
        model_path = trainer.save_model()

        # Check file exists
        assert model_path.exists()
        assert model_path.name == "test_experiment.txt"
        assert model_path.parent == self.model_dir

    def test_save_model_without_training_raises_error(self) -> None:
        """Save without training raises ValueError."""
        trainer = BaselineTrainer()

        with pytest.raises(ValueError, match="not trained yet"):
            trainer.save_model()

    @pytest.mark.skip(reason="Requires trained model - integration test for Phase 6")
    def test_load_model(self) -> None:
        """Load trained model from disk."""
        # Train and save model first
        config = StrategyConfig(training=TrainingConfig(model_dir=self.model_dir))

        trainer1 = BaselineTrainer(config)
        trainer1.train()
        model_path = trainer1.save_model()

        # Load model in new trainer
        trainer2 = BaselineTrainer(config)
        loaded_model = trainer2.load_model(model_path)

        assert loaded_model is not None
        assert trainer2.model is not None
        assert trainer2.best_iteration == trainer1.best_iteration

        # Make predictions with loaded model
        X_test = pd.DataFrame(np.random.randn(50, 158))
        predictions = trainer2.predict(X_test)
        assert predictions.shape == (50,)

    def test_module_imports(self) -> None:
        """Module imports work correctly."""
        from strategies.alpha_baseline.train import (
            BaselineTrainer,
            train_baseline_model,
        )

        assert BaselineTrainer is not None
        assert callable(train_baseline_model)


class TestTrainBaselineModel:
    """Tests for train_baseline_model convenience function."""

    @pytest.mark.skip(reason="Requires Qlib data format - integration test for Phase 6")
    def test_train_with_default_config(self) -> None:
        """Train model with default config."""
        trainer = train_baseline_model()

        assert isinstance(trainer, BaselineTrainer)
        assert trainer.model is not None
        assert "valid_ic" in trainer.metrics

    @pytest.mark.skip(reason="Requires Qlib data format - integration test for Phase 6")
    def test_train_with_custom_config(self) -> None:
        """Train model with custom config."""
        config = StrategyConfig(
            data=DataConfig(symbols=["AAPL"]),
            model=ModelConfig(num_boost_round=10),
        )

        trainer = train_baseline_model(config)

        assert trainer.config.data.symbols == ["AAPL"]
        assert trainer.model is not None
