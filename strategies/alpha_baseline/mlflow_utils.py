"""
MLflow utilities for experiment tracking and model registry.

This module provides wrappers for MLflow integration with the baseline strategy,
including experiment tracking, parameter logging, metric logging, and artifact
management.

See /docs/IMPLEMENTATION_GUIDES/t2-baseline-strategy-qlib.md for details.
"""

import warnings
from pathlib import Path
from typing import Any

import mlflow
import mlflow.lightgbm
from mlflow.tracking import MlflowClient

from strategies.alpha_baseline.config import StrategyConfig


def initialize_mlflow(
    tracking_uri: str = "file:./artifacts/mlruns",
    experiment_name: str = "alpha_baseline",
) -> str:
    """
    Initialize MLflow tracking.

    Args:
        tracking_uri: MLflow tracking URI (local or remote)
        experiment_name: Name of MLflow experiment

    Returns:
        Experiment ID

    Example:
        >>> exp_id = initialize_mlflow(
        ...     tracking_uri="file:./artifacts/mlruns",
        ...     experiment_name="alpha_baseline"
        ... )
        >>> print(f"Experiment ID: {exp_id}")
        Experiment ID: 0

    Notes:
        - Creates experiment if it doesn't exist
        - Safe to call multiple times (idempotent)
        - Local tracking_uri creates directory automatically
    """
    # Set tracking URI
    mlflow.set_tracking_uri(tracking_uri)

    # Create or get experiment
    try:
        experiment = mlflow.get_experiment_by_name(experiment_name)
        if experiment is None:
            experiment_id = mlflow.create_experiment(experiment_name)
        else:
            experiment_id = experiment.experiment_id
    except Exception as e:
        warnings.warn(f"Failed to get/create experiment: {e}")
        experiment_id = "0"  # Default experiment

    # Set active experiment
    mlflow.set_experiment(experiment_name)

    return experiment_id


def log_config(config: StrategyConfig) -> None:
    """
    Log strategy configuration to MLflow.

    Logs all config parameters as MLflow params for experiment tracking.

    Args:
        config: Strategy configuration

    Example:
        >>> config = StrategyConfig()
        >>> with mlflow.start_run():
        ...     log_config(config)

    Notes:
        - Logs data config (symbols, dates)
        - Logs model hyperparameters
        - Logs training settings
        - Must be called within an active MLflow run
    """
    # Log data config
    mlflow.log_param("symbols", ",".join(config.data.symbols))
    mlflow.log_param("train_start", config.data.train_start)
    mlflow.log_param("train_end", config.data.train_end)
    mlflow.log_param("valid_start", config.data.valid_start)
    mlflow.log_param("valid_end", config.data.valid_end)
    mlflow.log_param("test_start", config.data.test_start)
    mlflow.log_param("test_end", config.data.test_end)

    # Log model hyperparameters
    model_params = config.model.to_dict()
    for key, value in model_params.items():
        mlflow.log_param(f"model_{key}", value)

    # Log training config
    mlflow.log_param("early_stopping_rounds", config.training.early_stopping_rounds)
    mlflow.log_param("save_best_only", config.training.save_best_only)


def log_metrics(metrics: dict[str, float], step: int | None = None) -> None:
    """
    Log metrics to MLflow.

    Args:
        metrics: Dictionary of metric name -> value
        step: Optional step number (for tracking across iterations)

    Example:
        >>> metrics = {
        ...     "train_mae": 0.0123,
        ...     "valid_mae": 0.0145,
        ...     "valid_ic": 0.0523
        ... }
        >>> with mlflow.start_run():
        ...     log_metrics(metrics)

    Notes:
        - Must be called within an active MLflow run
        - Supports step-based logging for iteration tracking
    """
    for name, value in metrics.items():
        mlflow.log_metric(name, value, step=step)


def log_model(
    model: Any,
    artifact_path: str = "model",
    registered_model_name: str | None = None,
) -> None:
    """
    Log LightGBM model to MLflow.

    Args:
        model: Trained LightGBM model
        artifact_path: Path within run artifacts to store model
        registered_model_name: Name for model registry (None = don't register)

    Example:
        >>> import lightgbm as lgb
        >>> model = lgb.Booster(model_file="model.txt")
        >>> with mlflow.start_run():
        ...     log_model(model, registered_model_name="alpha_baseline_prod")

    Notes:
        - Automatically logs model signature and input example
        - Registers model if registered_model_name provided
        - Model can be loaded later with mlflow.lightgbm.load_model()
    """
    mlflow.lightgbm.log_model(
        lgb_model=model,
        artifact_path=artifact_path,
        registered_model_name=registered_model_name,
    )


def log_artifact(local_path: Path, artifact_path: str | None = None) -> None:
    """
    Log artifact file to MLflow.

    Args:
        local_path: Path to local file
        artifact_path: Path within run artifacts (None = root)

    Example:
        >>> with mlflow.start_run():
        ...     log_artifact(Path("feature_importance.png"), "plots")

    Notes:
        - Useful for plots, reports, configs, etc.
        - File is copied to MLflow artifact store
        - Can be retrieved later from run
    """
    mlflow.log_artifact(str(local_path), artifact_path=artifact_path)


def get_or_create_run(
    experiment_id: str,
    run_name: str | None = None,
    tags: dict[str, str] | None = None,
) -> mlflow.ActiveRun:
    """
    Get existing run or create new one.

    Args:
        experiment_id: MLflow experiment ID
        run_name: Name for the run (auto-generated if None)
        tags: Dictionary of tags to add to run

    Returns:
        Active MLflow run context

    Example:
        >>> exp_id = initialize_mlflow()
        >>> with get_or_create_run(exp_id, run_name="baseline_v1") as run:
        ...     mlflow.log_param("test", "value")

    Notes:
        - Returns context manager for use with 'with' statement
        - Auto-generates run_name if not provided
        - Tags useful for filtering runs later
    """
    return mlflow.start_run(
        experiment_id=experiment_id,
        run_name=run_name,
        tags=tags,
    )


def end_run() -> None:
    """
    End active MLflow run.

    Example:
        >>> mlflow.start_run()
        >>> mlflow.log_param("test", "value")
        >>> end_run()

    Notes:
        - Safe to call even if no active run
        - Automatically called when exiting 'with' context
    """
    mlflow.end_run()


def get_best_run(
    experiment_name: str,
    metric_name: str = "valid_ic",
    ascending: bool = False,
) -> mlflow.entities.Run | None:
    """
    Get best run from experiment by metric.

    Args:
        experiment_name: Name of experiment to search
        metric_name: Metric to optimize (e.g., "valid_ic", "valid_mae")
        ascending: True for minimize (MAE), False for maximize (IC)

    Returns:
        Best run or None if experiment has no runs

    Example:
        >>> best_run = get_best_run("alpha_baseline", "valid_ic", ascending=False)
        >>> if best_run:
        ...     print(f"Best IC: {best_run.data.metrics['valid_ic']:.4f}")
        Best IC: 0.0523

    Notes:
        - Useful for comparing runs and finding best model
        - Can use any logged metric for comparison
        - Returns full Run object with params, metrics, artifacts
    """
    client = MlflowClient()

    # Get experiment
    experiment = client.get_experiment_by_name(experiment_name)
    if experiment is None:
        return None

    # Search runs
    runs = client.search_runs(
        experiment_ids=[experiment.experiment_id],
        order_by=[f"metrics.{metric_name} {'ASC' if ascending else 'DESC'}"],
        max_results=1,
    )

    return runs[0] if runs else None


def load_model_from_run(run_id: str, artifact_path: str = "model") -> Any:
    """
    Load model from MLflow run.

    Args:
        run_id: MLflow run ID
        artifact_path: Path to model within run artifacts

    Returns:
        Loaded model

    Example:
        >>> run_id = "abc123def456"
        >>> model = load_model_from_run(run_id)
        >>> predictions = model.predict(X_test)

    Notes:
        - Loads LightGBM model with mlflow.lightgbm.load_model()
        - Model is ready to use for predictions
        - Can load from local or remote tracking server
    """
    model_uri = f"runs:/{run_id}/{artifact_path}"
    return mlflow.lightgbm.load_model(model_uri)


def compare_runs(
    experiment_name: str,
    metric_names: list[str] = ["valid_ic", "valid_mae", "train_ic", "train_mae"],
    max_results: int = 10,
) -> Any | None:
    """
    Compare recent runs from experiment.

    Args:
        experiment_name: Name of experiment
        metric_names: Metrics to include in comparison
        max_results: Maximum number of runs to return

    Returns:
        Pandas DataFrame with run comparison or None

    Example:
        >>> df = compare_runs("alpha_baseline", max_results=5)
        >>> print(df[['run_id', 'valid_ic', 'valid_mae']].head())
                run_id  valid_ic  valid_mae
        0  abc123...    0.0523    0.0145
        1  def456...    0.0498    0.0152

    Notes:
        - Useful for analyzing hyperparameter impact
        - Can export to CSV, plot, etc.
        - Requires pandas for DataFrame creation
    """
    try:
        import pandas as pd
    except ImportError:
        warnings.warn("pandas not installed, cannot create comparison DataFrame")
        return None

    client = MlflowClient()

    # Get experiment
    experiment = client.get_experiment_by_name(experiment_name)
    if experiment is None:
        return None

    # Search runs
    runs = client.search_runs(
        experiment_ids=[experiment.experiment_id],
        max_results=max_results,
    )

    if not runs:
        return None

    # Build DataFrame
    data = []
    for run in runs:
        row = {
            "run_id": run.info.run_id,
            "run_name": run.info.run_name,
            "start_time": run.info.start_time,
            "status": run.info.status,
        }

        # Add metrics
        for metric_name in metric_names:
            row[metric_name] = run.data.metrics.get(metric_name, None)

        data.append(row)

    return pd.DataFrame(data)
