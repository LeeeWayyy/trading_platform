"""
Unit tests for strategy configuration.

Tests cover:
- DataConfig initialization and defaults
- ModelConfig initialization and to_dict conversion
- TrainingConfig initialization
- StrategyConfig composition and from_dict
"""

from pathlib import Path

import pytest

from strategies.alpha_baseline.config import (
    DataConfig,
    ModelConfig,
    TrainingConfig,
    StrategyConfig,
    DEFAULT_CONFIG,
)


class TestDataConfig:
    """Tests for DataConfig."""

    def test_default_initialization(self) -> None:
        """Initialize with defaults."""
        config = DataConfig()

        assert config.symbols == ["AAPL", "MSFT", "GOOGL"]
        assert config.data_dir == Path("data/adjusted")
        assert config.train_start == "2020-01-01"
        assert config.train_end == "2023-12-31"
        assert config.valid_start == "2024-01-01"
        assert config.valid_end == "2024-06-30"
        assert config.test_start == "2024-07-01"
        assert config.test_end == "2024-12-31"

    def test_custom_initialization(self) -> None:
        """Initialize with custom values."""
        config = DataConfig(
            symbols=["AAPL", "TSLA"],
            data_dir=Path("/custom/path"),
            train_start="2021-01-01",
            train_end="2022-12-31",
        )

        assert config.symbols == ["AAPL", "TSLA"]
        assert config.data_dir == Path("/custom/path")
        assert config.train_start == "2021-01-01"
        assert config.train_end == "2022-12-31"
        # Other fields should use defaults
        assert config.valid_start == "2024-01-01"


class TestModelConfig:
    """Tests for ModelConfig."""

    def test_default_initialization(self) -> None:
        """Initialize with defaults."""
        config = ModelConfig()

        assert config.objective == "regression"
        assert config.metric == "mae"
        assert config.boosting_type == "gbdt"
        assert config.num_boost_round == 100
        assert config.learning_rate == 0.05
        assert config.max_depth == 6
        assert config.num_leaves == 31
        assert config.verbose == -1
        assert config.seed == 42

    def test_custom_initialization(self) -> None:
        """Initialize with custom values."""
        config = ModelConfig(
            learning_rate=0.1,
            max_depth=8,
            num_boost_round=200,
        )

        assert config.learning_rate == 0.1
        assert config.max_depth == 8
        assert config.num_boost_round == 200
        # Other fields should use defaults
        assert config.objective == "regression"

    def test_to_dict(self) -> None:
        """Convert config to dictionary."""
        config = ModelConfig(learning_rate=0.1, max_depth=8)
        params = config.to_dict()

        assert isinstance(params, dict)
        assert params["learning_rate"] == 0.1
        assert params["max_depth"] == 8
        assert params["objective"] == "regression"
        assert params["metric"] == "mae"
        assert params["seed"] == 42

        # Check all expected keys are present
        expected_keys = [
            "objective",
            "metric",
            "boosting_type",
            "learning_rate",
            "max_depth",
            "num_leaves",
            "feature_fraction",
            "bagging_fraction",
            "bagging_freq",
            "min_data_in_leaf",
            "lambda_l1",
            "lambda_l2",
            "verbose",
            "seed",
            "num_threads",
        ]
        assert set(params.keys()) == set(expected_keys)


class TestTrainingConfig:
    """Tests for TrainingConfig."""

    def test_default_initialization(self) -> None:
        """Initialize with defaults."""
        config = TrainingConfig()

        assert config.early_stopping_rounds == 20
        assert config.save_best_only is True
        assert config.model_dir == Path("artifacts/models")
        assert config.experiment_name == "alpha_baseline"
        assert config.run_name is None

    def test_custom_initialization(self) -> None:
        """Initialize with custom values."""
        config = TrainingConfig(
            early_stopping_rounds=10,
            save_best_only=False,
            experiment_name="test_experiment",
            run_name="test_run",
        )

        assert config.early_stopping_rounds == 10
        assert config.save_best_only is False
        assert config.experiment_name == "test_experiment"
        assert config.run_name == "test_run"


class TestStrategyConfig:
    """Tests for StrategyConfig."""

    def test_default_initialization(self) -> None:
        """Initialize with defaults."""
        config = StrategyConfig()

        # Check sub-configs are initialized
        assert isinstance(config.data, DataConfig)
        assert isinstance(config.model, ModelConfig)
        assert isinstance(config.training, TrainingConfig)

        # Check defaults propagate
        assert config.data.symbols == ["AAPL", "MSFT", "GOOGL"]
        assert config.model.learning_rate == 0.05
        assert config.training.experiment_name == "alpha_baseline"

    def test_custom_initialization(self) -> None:
        """Initialize with custom sub-configs."""
        data_config = DataConfig(symbols=["AAPL"])
        model_config = ModelConfig(learning_rate=0.1)
        training_config = TrainingConfig(early_stopping_rounds=10)

        config = StrategyConfig(
            data=data_config,
            model=model_config,
            training=training_config,
        )

        assert config.data.symbols == ["AAPL"]
        assert config.model.learning_rate == 0.1
        assert config.training.early_stopping_rounds == 10

    def test_from_dict(self) -> None:
        """Create config from dictionary."""
        config_dict = {
            "data": {
                "symbols": ["AAPL", "TSLA"],
                "train_start": "2021-01-01",
            },
            "model": {
                "learning_rate": 0.1,
                "max_depth": 8,
            },
            "training": {
                "early_stopping_rounds": 10,
                "experiment_name": "test_exp",
            },
        }

        config = StrategyConfig.from_dict(config_dict)

        assert config.data.symbols == ["AAPL", "TSLA"]
        assert config.data.train_start == "2021-01-01"
        assert config.model.learning_rate == 0.1
        assert config.model.max_depth == 8
        assert config.training.early_stopping_rounds == 10
        assert config.training.experiment_name == "test_exp"

        # Check defaults for unspecified fields
        assert config.data.train_end == "2023-12-31"  # Default
        assert config.model.objective == "regression"  # Default

    def test_from_dict_empty(self) -> None:
        """Create config from empty dictionary uses all defaults."""
        config = StrategyConfig.from_dict({})

        assert config.data.symbols == ["AAPL", "MSFT", "GOOGL"]
        assert config.model.learning_rate == 0.05
        assert config.training.early_stopping_rounds == 20


class TestDefaultConfig:
    """Tests for DEFAULT_CONFIG."""

    def test_default_config_exists(self) -> None:
        """DEFAULT_CONFIG is initialized."""
        assert DEFAULT_CONFIG is not None
        assert isinstance(DEFAULT_CONFIG, StrategyConfig)

    def test_default_config_values(self) -> None:
        """DEFAULT_CONFIG has expected values."""
        assert DEFAULT_CONFIG.data.symbols == ["AAPL", "MSFT", "GOOGL"]
        assert DEFAULT_CONFIG.model.learning_rate == 0.05
        assert DEFAULT_CONFIG.training.experiment_name == "alpha_baseline"
