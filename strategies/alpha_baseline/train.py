"""
Model training pipeline for baseline strategy.

This module implements the end-to-end training pipeline:
1. Load data from T1DataProvider
2. Compute Alpha158 features
3. Train LightGBM model
4. Evaluate on validation set
5. Save best model

Integrates with MLflow for experiment tracking (Phase 4).

See /docs/IMPLEMENTATION_GUIDES/t2-baseline-strategy-qlib.md for details.
"""

from pathlib import Path
from typing import Optional, Tuple
import warnings

import pandas as pd
import numpy as np
import lightgbm as lgb
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score

from strategies.alpha_baseline.config import StrategyConfig, DEFAULT_CONFIG
from strategies.alpha_baseline.features import compute_features_and_labels


class BaselineTrainer:
    """
    Trainer for baseline strategy using LightGBM.

    This class encapsulates the complete training pipeline:
    - Data loading and feature engineering
    - Model training with early stopping
    - Validation and evaluation
    - Model persistence

    Attributes:
        config: Strategy configuration
        model: Trained LightGBM model (None before training)
        best_iteration: Best boosting iteration (from early stopping)
        metrics: Training and validation metrics

    Example:
        >>> from strategies.alpha_baseline.config import StrategyConfig
        >>> config = StrategyConfig()
        >>> trainer = BaselineTrainer(config)
        >>> trainer.train()
        >>> predictions = trainer.predict(X_test)

    See Also:
        - /docs/IMPLEMENTATION_GUIDES/t2-baseline-strategy-qlib.md
        - /docs/CONCEPTS/alpha158-features.md
    """

    def __init__(self, config: Optional[StrategyConfig] = None) -> None:
        """
        Initialize trainer with configuration.

        Args:
            config: Strategy configuration (uses DEFAULT_CONFIG if None)
        """
        self.config = config or DEFAULT_CONFIG
        self.model: Optional[lgb.Booster] = None
        self.best_iteration: int = 0
        self.metrics: dict = {}

    def load_data(
        self,
    ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
        """
        Load and prepare data for training.

        Uses configuration to determine:
        - Which symbols to load
        - Date ranges for train/valid/test splits
        - Feature computation settings

        Returns:
            Tuple of 6 DataFrames: (X_train, y_train, X_valid, y_valid, X_test, y_test)

        Example:
            >>> trainer = BaselineTrainer()
            >>> X_train, y_train, X_valid, y_valid, X_test, y_test = trainer.load_data()
            >>> print(f"Train: {X_train.shape}, Valid: {X_valid.shape}")
            Train: (3024, 158), Valid: (378, 158)
        """
        print("Loading data and computing features...")
        print(f"Symbols: {self.config.data.symbols}")
        print(f"Train: {self.config.data.train_start} to {self.config.data.train_end}")
        print(f"Valid: {self.config.data.valid_start} to {self.config.data.valid_end}")
        print(f"Test: {self.config.data.test_start} to {self.config.data.test_end}")

        # Compute features and labels for all splits
        X_train, y_train, X_valid, y_valid, X_test, y_test = compute_features_and_labels(
            symbols=self.config.data.symbols,
            train_start=self.config.data.train_start,
            train_end=self.config.data.train_end,
            valid_start=self.config.data.valid_start,
            valid_end=self.config.data.valid_end,
            test_start=self.config.data.test_start,
            test_end=self.config.data.test_end,
            data_dir=self.config.data.data_dir,
        )

        print(f"\nData loaded successfully:")
        print(f"  Train: {X_train.shape[0]} samples, {X_train.shape[1]} features")
        print(f"  Valid: {X_valid.shape[0]} samples, {X_valid.shape[1]} features")
        print(f"  Test:  {X_test.shape[0]} samples, {X_test.shape[1]} features")

        return X_train, y_train, X_valid, y_valid, X_test, y_test

    def train(
        self,
        X_train: Optional[pd.DataFrame] = None,
        y_train: Optional[pd.DataFrame] = None,
        X_valid: Optional[pd.DataFrame] = None,
        y_valid: Optional[pd.DataFrame] = None,
    ) -> lgb.Booster:
        """
        Train LightGBM model.

        If data not provided, loads it automatically using config.

        Args:
            X_train: Training features (auto-loaded if None)
            y_train: Training labels (auto-loaded if None)
            X_valid: Validation features (auto-loaded if None)
            y_valid: Validation labels (auto-loaded if None)

        Returns:
            Trained LightGBM Booster model

        Example:
            >>> trainer = BaselineTrainer()
            >>> model = trainer.train()
            >>> print(f"Best iteration: {trainer.best_iteration}")
            Best iteration: 78

        Notes:
            - Uses early stopping based on validation MAE
            - Saves best model if config.training.save_best_only = True
            - Computes and stores training metrics
        """
        # Load data if not provided
        if X_train is None or y_train is None or X_valid is None or y_valid is None:
            print("Data not provided, loading from config...")
            X_train, y_train, X_valid, y_valid, _, _ = self.load_data()

        # Convert to LightGBM datasets
        print("\nPreparing LightGBM datasets...")
        train_data = lgb.Dataset(X_train, label=y_train.values.ravel())
        valid_data = lgb.Dataset(X_valid, label=y_valid.values.ravel(), reference=train_data)

        # Get model parameters
        params = self.config.model.to_dict()

        # Train model with early stopping
        print("\nTraining LightGBM model...")
        print(f"Parameters: {params}")

        callbacks = []
        if self.config.training.early_stopping_rounds > 0:
            callbacks.append(
                lgb.early_stopping(
                    stopping_rounds=self.config.training.early_stopping_rounds,
                    verbose=False,
                )
            )

        # Suppress LightGBM warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")

            self.model = lgb.train(
                params,
                train_data,
                num_boost_round=self.config.model.num_boost_round,
                valid_sets=[train_data, valid_data],
                valid_names=["train", "valid"],
                callbacks=callbacks,
            )

        # Store best iteration
        self.best_iteration = self.model.best_iteration

        print(f"\nTraining complete!")
        print(f"Best iteration: {self.best_iteration}")

        # Evaluate on train and valid sets
        self._evaluate(X_train, y_train, X_valid, y_valid)

        # Save model if configured
        if self.config.training.save_best_only:
            self.save_model()

        return self.model

    def _evaluate(
        self,
        X_train: pd.DataFrame,
        y_train: pd.DataFrame,
        X_valid: pd.DataFrame,
        y_valid: pd.DataFrame,
    ) -> None:
        """
        Evaluate model on train and validation sets.

        Computes and stores:
        - MAE (Mean Absolute Error)
        - RMSE (Root Mean Squared Error)
        - R² (Coefficient of determination)
        - IC (Information Coefficient - correlation between pred and actual)

        Args:
            X_train: Training features
            y_train: Training labels
            X_valid: Validation features
            y_valid: Validation labels
        """
        # Get predictions
        y_train_pred = self.model.predict(X_train, num_iteration=self.best_iteration)
        y_valid_pred = self.model.predict(X_valid, num_iteration=self.best_iteration)

        # Flatten labels for sklearn metrics
        y_train_true = y_train.values.ravel()
        y_valid_true = y_valid.values.ravel()

        # Compute metrics
        train_mae = mean_absolute_error(y_train_true, y_train_pred)
        train_rmse = np.sqrt(mean_squared_error(y_train_true, y_train_pred))
        train_r2 = r2_score(y_train_true, y_train_pred)
        train_ic = np.corrcoef(y_train_true, y_train_pred)[0, 1]

        valid_mae = mean_absolute_error(y_valid_true, y_valid_pred)
        valid_rmse = np.sqrt(mean_squared_error(y_valid_true, y_valid_pred))
        valid_r2 = r2_score(y_valid_true, y_valid_pred)
        valid_ic = np.corrcoef(y_valid_true, y_valid_pred)[0, 1]

        # Store metrics
        self.metrics = {
            "train_mae": train_mae,
            "train_rmse": train_rmse,
            "train_r2": train_r2,
            "train_ic": train_ic,
            "valid_mae": valid_mae,
            "valid_rmse": valid_rmse,
            "valid_r2": valid_r2,
            "valid_ic": valid_ic,
        }

        # Print metrics
        print("\n" + "=" * 50)
        print("Evaluation Metrics")
        print("=" * 50)
        print(f"{'Metric':<15} {'Train':>15} {'Valid':>15}")
        print("-" * 50)
        print(f"{'MAE':<15} {train_mae:>15.6f} {valid_mae:>15.6f}")
        print(f"{'RMSE':<15} {train_rmse:>15.6f} {valid_rmse:>15.6f}")
        print(f"{'R²':<15} {train_r2:>15.6f} {valid_r2:>15.6f}")
        print(f"{'IC':<15} {train_ic:>15.6f} {valid_ic:>15.6f}")
        print("=" * 50)

    def predict(
        self,
        X: pd.DataFrame,
        num_iteration: Optional[int] = None,
    ) -> np.ndarray:
        """
        Make predictions using trained model.

        Args:
            X: Features to predict on
            num_iteration: Number of iterations to use (uses best_iteration if None)

        Returns:
            Array of predictions

        Raises:
            ValueError: If model not trained yet

        Example:
            >>> trainer = BaselineTrainer()
            >>> trainer.train()
            >>> predictions = trainer.predict(X_test)
            >>> print(predictions.shape)
            (378,)
        """
        if self.model is None:
            raise ValueError("Model not trained yet. Call train() first.")

        if num_iteration is None:
            num_iteration = self.best_iteration

        return self.model.predict(X, num_iteration=num_iteration)

    def save_model(self, path: Optional[Path] = None) -> Path:
        """
        Save trained model to disk.

        Args:
            path: Path to save model (uses config.training.model_dir if None)

        Returns:
            Path where model was saved

        Example:
            >>> trainer = BaselineTrainer()
            >>> trainer.train()
            >>> model_path = trainer.save_model()
            >>> print(f"Model saved to: {model_path}")
            Model saved to: artifacts/models/alpha_baseline.txt
        """
        if self.model is None:
            raise ValueError("Model not trained yet. Call train() first.")

        if path is None:
            # Create model directory if it doesn't exist
            self.config.training.model_dir.mkdir(parents=True, exist_ok=True)

            # Use experiment name for filename
            filename = f"{self.config.training.experiment_name}.txt"
            path = self.config.training.model_dir / filename

        # Save model
        self.model.save_model(str(path))
        print(f"\nModel saved to: {path}")

        return path

    def load_model(self, path: Path) -> lgb.Booster:
        """
        Load trained model from disk.

        Args:
            path: Path to saved model file

        Returns:
            Loaded LightGBM Booster model

        Example:
            >>> trainer = BaselineTrainer()
            >>> model = trainer.load_model(Path("artifacts/models/alpha_baseline.txt"))
            >>> predictions = trainer.predict(X_test)
        """
        self.model = lgb.Booster(model_file=str(path))
        self.best_iteration = self.model.best_iteration
        print(f"Model loaded from: {path}")
        print(f"Best iteration: {self.best_iteration}")

        return self.model


def train_baseline_model(config: Optional[StrategyConfig] = None) -> BaselineTrainer:
    """
    Convenience function to train baseline model.

    This is the main entry point for training.

    Args:
        config: Strategy configuration (uses DEFAULT_CONFIG if None)

    Returns:
        Trained BaselineTrainer instance

    Example:
        >>> from strategies.alpha_baseline.config import StrategyConfig
        >>> config = StrategyConfig()
        >>> trainer = train_baseline_model(config)
        >>> print(f"Validation IC: {trainer.metrics['valid_ic']:.4f}")
        Validation IC: 0.0523

    Notes:
        - Loads data automatically
        - Trains model with early stopping
        - Saves best model
        - Returns trainer for further use (predictions, evaluation, etc.)
    """
    trainer = BaselineTrainer(config)
    trainer.train()
    return trainer


if __name__ == "__main__":
    # Train model with default configuration
    print("Training baseline model with default configuration...")
    trainer = train_baseline_model()
    print("\nTraining complete!")
    print(f"Validation IC: {trainer.metrics['valid_ic']:.4f}")
