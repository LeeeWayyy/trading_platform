"""
Configuration for baseline strategy.

This module centralizes all configuration for the alpha baseline strategy,
including model hyperparameters, training settings, and data splits.

See /docs/IMPLEMENTATION_GUIDES/t2-baseline-strategy-qlib.md for details.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DataConfig:
    """
    Configuration for data loading and preprocessing.

    Attributes:
        symbols: List of stock symbols to trade
        data_dir: Directory containing T1's adjusted Parquet files
        train_start: Training period start date (YYYY-MM-DD)
        train_end: Training period end date (YYYY-MM-DD)
        valid_start: Validation period start date (YYYY-MM-DD)
        valid_end: Validation period end date (YYYY-MM-DD)
        test_start: Test period start date (YYYY-MM-DD)
        test_end: Test period end date (YYYY-MM-DD)

    Example:
        >>> config = DataConfig(
        ...     symbols=["AAPL", "MSFT", "GOOGL"],
        ...     train_start="2020-01-01",
        ...     train_end="2023-12-31"
        ... )
    """

    symbols: list[str] = field(default_factory=lambda: ["AAPL", "MSFT", "GOOGL"])
    data_dir: Path = Path("data/adjusted")

    # Data splits (default: 2020-2023 train, 2024 H1 valid, 2024 H2 test)
    train_start: str = "2020-01-01"
    train_end: str = "2023-12-31"
    valid_start: str = "2024-01-01"
    valid_end: str = "2024-06-30"
    test_start: str = "2024-07-01"
    test_end: str = "2024-12-31"


@dataclass
class ModelConfig:
    """
    Configuration for LightGBM model.

    These hyperparameters are based on Qlib's recommended settings for
    financial time series prediction. They balance performance and
    overfitting prevention.

    Attributes:
        objective: Learning objective (regression for return prediction)
        metric: Evaluation metric (MAE for robust loss)
        boosting_type: Boosting algorithm (gbdt = gradient boosting)
        num_boost_round: Number of boosting iterations
        learning_rate: Step size for gradient descent
        max_depth: Maximum tree depth (prevents overfitting)
        num_leaves: Maximum leaves per tree
        feature_fraction: Fraction of features to use per tree
        bagging_fraction: Fraction of samples to use per tree
        bagging_freq: Frequency of bagging
        min_data_in_leaf: Minimum samples in a leaf node
        lambda_l1: L1 regularization
        lambda_l2: L2 regularization
        verbose: Logging verbosity (-1 = silent, 0 = warning, 1+ = info)
        seed: Random seed for reproducibility
        num_threads: Number of threads for training

    Example:
        >>> config = ModelConfig(learning_rate=0.05, max_depth=6)
    """

    # Objective and metric
    objective: str = "regression"
    metric: str = "mae"  # Mean Absolute Error

    # Boosting type
    boosting_type: str = "gbdt"  # Gradient Boosting Decision Tree

    # Tree parameters
    num_boost_round: int = 100  # Number of boosting iterations
    learning_rate: float = 0.05  # Learning rate (eta)
    max_depth: int = 6  # Maximum tree depth
    num_leaves: int = 31  # Maximum leaves per tree (2^depth - 1)

    # Feature sampling
    feature_fraction: float = 0.8  # Use 80% of features per tree
    bagging_fraction: float = 0.8  # Use 80% of samples per tree
    bagging_freq: int = 5  # Bagging every 5 iterations

    # Regularization
    min_data_in_leaf: int = 20  # Minimum samples per leaf
    lambda_l1: float = 0.1  # L1 regularization
    lambda_l2: float = 0.1  # L2 regularization

    # Other settings
    verbose: int = -1  # Silent mode
    seed: int = 42  # Random seed for reproducibility
    num_threads: int = 4  # Number of threads

    def to_dict(self) -> dict[str, Any]:
        """
        Convert config to dictionary for LightGBM.

        Returns:
            Dictionary of hyperparameters.

        Example:
            >>> config = ModelConfig()
            >>> params = config.to_dict()
            >>> print(params['learning_rate'])
            0.05
        """
        return {
            "objective": self.objective,
            "metric": self.metric,
            "boosting_type": self.boosting_type,
            "learning_rate": self.learning_rate,
            "max_depth": self.max_depth,
            "num_leaves": self.num_leaves,
            "feature_fraction": self.feature_fraction,
            "bagging_fraction": self.bagging_fraction,
            "bagging_freq": self.bagging_freq,
            "min_data_in_leaf": self.min_data_in_leaf,
            "lambda_l1": self.lambda_l1,
            "lambda_l2": self.lambda_l2,
            "verbose": self.verbose,
            "seed": self.seed,
            "num_threads": self.num_threads,
        }


@dataclass
class TrainingConfig:
    """
    Configuration for training pipeline.

    Attributes:
        early_stopping_rounds: Stop if valid metric doesn't improve for N rounds
        save_best_only: Save only the best model (by validation metric)
        model_dir: Directory to save trained models
        experiment_name: MLflow experiment name
        run_name: MLflow run name (optional, auto-generated if None)

    Example:
        >>> config = TrainingConfig(
        ...     early_stopping_rounds=10,
        ...     experiment_name="alpha_baseline_v1"
        ... )
    """

    early_stopping_rounds: int = 20
    save_best_only: bool = True
    model_dir: Path = Path("artifacts/models")
    experiment_name: str = "alpha_baseline"
    run_name: str | None = None  # Auto-generated if None


@dataclass
class StrategyConfig:
    """
    Complete strategy configuration.

    Combines all sub-configurations for easy management.

    Attributes:
        data: Data loading configuration
        model: Model hyperparameters
        training: Training pipeline configuration

    Example:
        >>> config = StrategyConfig()
        >>> print(config.data.symbols)
        ['AAPL', 'MSFT', 'GOOGL']
        >>> print(config.model.learning_rate)
        0.05
        >>> print(config.training.experiment_name)
        alpha_baseline
    """

    data: DataConfig = field(default_factory=DataConfig)
    model: ModelConfig = field(default_factory=ModelConfig)
    training: TrainingConfig = field(default_factory=TrainingConfig)

    @classmethod
    def from_dict(cls, config_dict: dict[str, Any]) -> "StrategyConfig":
        """
        Create StrategyConfig from dictionary.

        Args:
            config_dict: Dictionary with 'data', 'model', 'training' keys

        Returns:
            StrategyConfig instance

        Example:
            >>> config_dict = {
            ...     "data": {"symbols": ["AAPL", "MSFT"]},
            ...     "model": {"learning_rate": 0.1},
            ...     "training": {"early_stopping_rounds": 10}
            ... }
            >>> config = StrategyConfig.from_dict(config_dict)
        """
        return cls(
            data=DataConfig(**config_dict.get("data", {})),
            model=ModelConfig(**config_dict.get("model", {})),
            training=TrainingConfig(**config_dict.get("training", {})),
        )


# Default configuration instance
DEFAULT_CONFIG = StrategyConfig()
