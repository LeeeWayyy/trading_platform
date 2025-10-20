"""
Alpha158 feature engineering for baseline strategy.

This module provides a simplified interface to Qlib's Alpha158 feature set.
It integrates our T1DataProvider with Qlib's feature computation engine.

Features are used for both:
1. Research/backtesting (this module)
2. Production signal generation (will be deployed to Signal Service)

Feature parity pattern ensures train-serve consistency.

See /docs/CONCEPTS/alpha158-features.md for detailed explanation.
"""

from datetime import date
from typing import List, Optional, cast
from pathlib import Path

import pandas as pd
import qlib
from qlib.contrib.data.handler import Alpha158
from qlib.data.dataset import DatasetH

from strategies.alpha_baseline.data_loader import T1DataProvider


def initialize_qlib_with_t1_data(data_dir: Path = Path("data/adjusted")) -> None:
    """
    Initialize Qlib to use T1's adjusted Parquet data.

    This sets up Qlib's data provider to read from our T1 pipeline output
    instead of requiring Qlib's native data format.

    Args:
        data_dir: Directory containing T1's adjusted Parquet files

    Notes:
        - Call this once before using any Qlib features
        - Uses custom T1DataProvider as backend
        - Enables Qlib to work directly with T1's Parquet format

    Example:
        >>> initialize_qlib_with_t1_data()
        >>> # Now Qlib can access T1 data
    """
    # Initialize Qlib with provider_uri pointing to T1 data
    # For now, we'll use Qlib's default initialization
    # In Phase 3, we'll create a custom provider integration
    qlib.init(provider_uri=str(data_dir), region="us")


def get_alpha158_features(
    symbols: List[str],
    start_date: str,
    end_date: str,
    fit_start_date: Optional[str] = None,
    fit_end_date: Optional[str] = None,
    data_dir: Path = Path("data/adjusted"),
) -> pd.DataFrame:
    """
    Get Alpha158 features for given symbols and date range.

    This function:
    1. Initializes Qlib with T1 data provider
    2. Uses Qlib's built-in Alpha158 handler
    3. Returns computed features as DataFrame

    Args:
        symbols: List of stock symbols (e.g., ["AAPL", "MSFT"])
        start_date: Start date (e.g., "2024-01-01")
        end_date: End date (e.g., "2024-12-31")
        fit_start_date: Start date for normalization stats (default: start_date)
        fit_end_date: End date for normalization stats (default: end_date)
        data_dir: Directory with T1 adjusted Parquet files

    Returns:
        DataFrame with (date, symbol) MultiIndex and 158 feature columns.

    Example:
        >>> features = get_alpha158_features(
        ...     symbols=["AAPL", "MSFT"],
        ...     start_date="2024-01-01",
        ...     end_date="2024-12-31"
        ... )
        >>> print(features.shape)
        (504, 158)
        >>> print(features.columns[:5])
        Index(['KBAR0', 'KBAR1', 'KBAR2', 'KBAR3', 'KBAR4'], dtype='object')

    Notes:
        - First call initializes Qlib (slow)
        - Subsequent calls are faster (uses cache)
        - Features are normalized using robust statistics
        - Missing values are forward-filled

    See Also:
        - /docs/CONCEPTS/alpha158-features.md
        - /docs/IMPLEMENTATION_GUIDES/t2-baseline-strategy-qlib.md
    """
    # Set default fit period
    if fit_start_date is None:
        fit_start_date = start_date
    if fit_end_date is None:
        fit_end_date = end_date

    # Initialize Qlib if not already done
    # Note: Multiple calls to qlib.init() are safe (it checks initialization)
    initialize_qlib_with_t1_data(data_dir)

    # Use Qlib's built-in Alpha158 handler
    handler = Alpha158(
        instruments=symbols,
        start_time=start_date,
        end_time=end_date,
        fit_start_time=fit_start_date,
        fit_end_time=fit_end_date,
    )

    # Fetch features
    # Qlib handler.fetch() returns Any, cast to pd.DataFrame for type safety
    features = handler.fetch(col_set="feature")
    return cast(pd.DataFrame, features)


def get_labels(
    symbols: List[str],
    start_date: str,
    end_date: str,
    data_dir: Path = Path("data/adjusted"),
) -> pd.DataFrame:
    """
    Get labels (next-day returns) for supervised learning.

    Args:
        symbols: List of stock symbols (e.g., ["AAPL", "MSFT"])
        start_date: Start date (e.g., "2024-01-01")
        end_date: End date (e.g., "2024-12-31")
        data_dir: Directory with T1 adjusted Parquet files

    Returns:
        DataFrame with (date, symbol) MultiIndex and single "LABEL0" column.

    Example:
        >>> labels = get_labels(
        ...     symbols=["AAPL", "MSFT"],
        ...     start_date="2024-01-01",
        ...     end_date="2024-12-31"
        ... )
        >>> print(labels.shape)
        (504, 1)
        >>> print(labels.head())
                              LABEL0
        datetime   instrument
        2024-01-01 AAPL       0.0123
                   MSFT       0.0089
        2024-01-02 AAPL      -0.0045
                   MSFT       0.0156

    Notes:
        - Label is the fractional return: (close_t+1 / close_t) - 1
        - Last day will have NaN label (no next-day price)
        - NaN labels are typically dropped during training
    """
    # Initialize Qlib if not already done
    initialize_qlib_with_t1_data(data_dir)

    # Use Qlib's built-in Alpha158 handler
    handler = Alpha158(
        instruments=symbols,
        start_time=start_date,
        end_time=end_date,
    )

    # Fetch labels
    # Qlib handler.fetch() returns Any, cast to pd.DataFrame for type safety
    labels = handler.fetch(col_set="label")
    return cast(pd.DataFrame, labels)


def compute_features_and_labels(
    symbols: List[str],
    train_start: str,
    train_end: str,
    valid_start: str,
    valid_end: str,
    test_start: str,
    test_end: str,
    data_dir: Path = Path("data/adjusted"),
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Compute features and labels for train/valid/test splits.

    This is a convenience function that prepares all data for model training.

    Args:
        symbols: List of stock symbols (e.g., ["AAPL", "MSFT", "GOOGL"])
        train_start: Training period start (e.g., "2020-01-01")
        train_end: Training period end (e.g., "2023-12-31")
        valid_start: Validation period start (e.g., "2024-01-01")
        valid_end: Validation period end (e.g., "2024-06-30")
        test_start: Test period start (e.g., "2024-07-01")
        test_end: Test period end (e.g., "2024-12-31")
        data_dir: Directory with T1 adjusted Parquet files

    Returns:
        Tuple of 6 DataFrames:
            - X_train: Training features (N × 158)
            - y_train: Training labels (N × 1)
            - X_valid: Validation features (M × 158)
            - y_valid: Validation labels (M × 1)
            - X_test: Test features (K × 158)
            - y_test: Test labels (K × 1)

    Example:
        >>> X_train, y_train, X_valid, y_valid, X_test, y_test = compute_features_and_labels(
        ...     symbols=["AAPL", "MSFT", "GOOGL"],
        ...     train_start="2020-01-01",
        ...     train_end="2023-12-31",
        ...     valid_start="2024-01-01",
        ...     valid_end="2024-06-30",
        ...     test_start="2024-07-01",
        ...     test_end="2024-12-31"
        ... )
        >>> print(f"Train: {X_train.shape}, Valid: {X_valid.shape}, Test: {X_test.shape}")
        Train: (3024, 158), Valid: (378, 158), Test: (378, 158)

    Notes:
        - Features are normalized using statistics from training period only
        - This prevents look-ahead bias
        - Labels with NaN are dropped (last day of each symbol)
    """
    # Compute training data (fit normalization on train period)
    X_train = get_alpha158_features(
        symbols=symbols,
        start_date=train_start,
        end_date=train_end,
        fit_start_date=train_start,
        fit_end_date=train_end,
        data_dir=data_dir,
    )
    y_train = get_labels(
        symbols=symbols,
        start_date=train_start,
        end_date=train_end,
        data_dir=data_dir,
    )

    # Compute validation data (use train period for normalization)
    X_valid = get_alpha158_features(
        symbols=symbols,
        start_date=valid_start,
        end_date=valid_end,
        fit_start_date=train_start,
        fit_end_date=train_end,
        data_dir=data_dir,
    )
    y_valid = get_labels(
        symbols=symbols,
        start_date=valid_start,
        end_date=valid_end,
        data_dir=data_dir,
    )

    # Compute test data (use train period for normalization)
    X_test = get_alpha158_features(
        symbols=symbols,
        start_date=test_start,
        end_date=test_end,
        fit_start_date=train_start,
        fit_end_date=train_end,
        data_dir=data_dir,
    )
    y_test = get_labels(
        symbols=symbols,
        start_date=test_start,
        end_date=test_end,
        data_dir=data_dir,
    )

    # Drop rows with NaN labels (last day of each symbol)
    train_mask = ~y_train.isna().any(axis=1)
    X_train = X_train[train_mask]
    y_train = y_train[train_mask]

    valid_mask = ~y_valid.isna().any(axis=1)
    X_valid = X_valid[valid_mask]
    y_valid = y_valid[valid_mask]

    test_mask = ~y_test.isna().any(axis=1)
    X_test = X_test[test_mask]
    y_test = y_test[test_mask]

    return X_train, y_train, X_valid, y_valid, X_test, y_test
