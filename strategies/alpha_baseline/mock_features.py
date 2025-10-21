"""
Mock feature generator for P3 testing.

This is a temporary solution for testing P1-P3 without full Qlib integration.
It generates simple technical features directly from T1 Parquet data.

For production, use the full Alpha158 features from features.py with proper
Qlib data format.
"""

from datetime import datetime, timedelta
from pathlib import Path
from typing import cast

import numpy as np
import pandas as pd
import polars as pl


def get_mock_alpha158_features(
    symbols: list[str],
    start_date: str,
    end_date: str,
    data_dir: Path = Path("data/adjusted"),
) -> pd.DataFrame:
    """
    Generate mock Alpha158 features from T1 data for testing.

    This function creates a simplified feature set (158 features) from OHLCV data.
    It's designed for P3 testing when full Qlib integration isn't available.

    Args:
        symbols: List of stock symbols (e.g., ["AAPL", "MSFT"])
        start_date: Start date string (e.g., "2025-10-16")
        end_date: End date string (e.g., "2025-10-16")
        data_dir: Directory with T1 adjusted Parquet files

    Returns:
        DataFrame with (datetime, instrument) MultiIndex and 158 feature columns.

    Notes:
        - This is a MOCK implementation for testing only
        - Features are simple technical indicators, not true Alpha158
        - For production, use get_alpha158_features() with proper Qlib setup
    """
    # Convert date strings to date objects
    start_dt = datetime.strptime(start_date, "%Y-%m-%d").date()
    end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()

    # Load historical data (need ~60 days for feature computation)
    lookback_start = start_dt - timedelta(days=60)

    all_data = []
    for symbol in symbols:
        # Find Parquet file for this symbol
        parquet_files = list(data_dir.rglob(f"*/{symbol}.parquet"))

        if not parquet_files:
            raise FileNotFoundError(f"No data found for symbol: {symbol}")

        # Try each file until we find one with data in the target date range
        df = None
        for parquet_file in sorted(parquet_files, reverse=True):
            candidate_df = pl.read_parquet(parquet_file)

            # Filter to lookback period
            filtered_df = candidate_df.filter(
                (pl.col("date") >= pl.lit(lookback_start)) & (pl.col("date") <= pl.lit(end_dt))
            )

            # If this file has data in our date range, use it
            if len(filtered_df) > 0:
                df = filtered_df
                break

        if df is None or len(df) == 0:
            raise FileNotFoundError(
                f"No data found for symbol {symbol} in date range {lookback_start} to {end_dt}"
            )

        # Sort by date
        df = df.sort("date")

        all_data.append(df)

    if not all_data:
        raise ValueError("No data loaded for any symbols")

    # Concatenate all symbols
    combined = pl.concat(all_data)

    # Convert to Pandas for easier manipulation
    pandas_df: pd.DataFrame = combined.to_pandas()

    # Compute simple features for each symbol
    feature_dfs = []

    for symbol in symbols:
        symbol_df = pandas_df[pandas_df["symbol"] == symbol].copy()

        if len(symbol_df) == 0:
            continue

        # Compute simple technical features
        features = compute_simple_features(symbol_df)

        # Filter to requested date range
        features = features[
            (features.index >= pd.Timestamp(start_dt)) & (features.index <= pd.Timestamp(end_dt))
        ]

        # Add symbol level
        features["instrument"] = symbol

        feature_dfs.append(features)

    if not feature_dfs:
        raise ValueError(f"No features computed for date range {start_date} to {end_date}")

    # Concatenate all symbols
    all_features = pd.concat(feature_dfs)

    # Create MultiIndex (datetime, instrument) matching Qlib format
    all_features = all_features.reset_index()
    all_features = all_features.rename(columns={"date": "datetime"})
    all_features = all_features.set_index(["datetime", "instrument"])

    # Select only feature columns (drop OHLCV)
    feature_cols = [col for col in all_features.columns if col.startswith("feature_")]
    all_features = all_features[feature_cols]

    return all_features


def compute_simple_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute simple technical features from OHLCV data.

    Creates 158 features to match Alpha158 dimensionality:
    - Price-based: returns, moving averages, volatility
    - Volume-based: volume ratios, trends
    - Technical: RSI, momentum, MACD-like indicators

    Args:
        df: DataFrame with columns [date, symbol, open, high, low, close, volume]

    Returns:
        DataFrame indexed by date with 158 feature columns
    """
    df = df.copy()
    df = df.set_index("date")
    df.index = pd.to_datetime(df.index)

    features = pd.DataFrame(index=df.index)

    # Extract OHLCV
    close = df["close"]
    high = df["high"]
    low = df["low"]
    open_ = df["open"]
    volume = df["volume"]

    feature_idx = 0

    # 1. Returns at various horizons (20 features)
    for period in [1, 2, 3, 5, 10, 20, 30, 60]:
        features[f"feature_{feature_idx}"] = close.pct_change(period)
        feature_idx += 1
        features[f"feature_{feature_idx}"] = np.log(close / close.shift(period))
        feature_idx += 1
        if feature_idx >= 20:
            break

    # 2. Moving averages and crossovers (30 features)
    for window in [5, 10, 20, 30, 60]:
        ma = close.rolling(window).mean()
        features[f"feature_{feature_idx}"] = close / ma - 1
        feature_idx += 1
        features[f"feature_{feature_idx}"] = ma.pct_change()
        feature_idx += 1

        # Volume MA
        vol_ma = volume.rolling(window).mean()
        features[f"feature_{feature_idx}"] = volume / vol_ma - 1
        feature_idx += 1

        if feature_idx >= 50:
            break

    # 3. Volatility features (20 features)
    for window in [5, 10, 20, 30, 60]:
        features[f"feature_{feature_idx}"] = close.pct_change().rolling(window).std()
        feature_idx += 1
        features[f"feature_{feature_idx}"] = (high - low) / close
        feature_idx += 1
        features[f"feature_{feature_idx}"] = (high - low).rolling(window).mean() / close
        feature_idx += 1

        if feature_idx >= 70:
            break

    # 4. Momentum indicators (30 features)
    for period in [5, 10, 14, 20, 30]:
        # RSI-like
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(period).mean()  # type: ignore[operator]
        loss = (-delta.where(delta < 0, 0)).rolling(period).mean()  # type: ignore[operator]
        rs = gain / (loss + 1e-10)
        features[f"feature_{feature_idx}"] = 100 - (100 / (1 + rs))
        feature_idx += 1

        # Rate of change
        features[f"feature_{feature_idx}"] = (close - close.shift(period)) / close.shift(period)
        feature_idx += 1

        # Momentum
        features[f"feature_{feature_idx}"] = close - close.shift(period)
        feature_idx += 1

        if feature_idx >= 100:
            break

    # 5. Volume features (20 features)
    for window in [5, 10, 20, 30]:
        features[f"feature_{feature_idx}"] = volume / volume.rolling(window).mean()
        feature_idx += 1
        features[f"feature_{feature_idx}"] = volume.pct_change()
        feature_idx += 1
        features[f"feature_{feature_idx}"] = (volume * close).rolling(window).sum()
        feature_idx += 1

        if feature_idx >= 120:
            break

    # 6. Price range features (20 features)
    for window in [5, 10, 20]:
        high_ma = high.rolling(window).max()
        low_ma = low.rolling(window).min()
        features[f"feature_{feature_idx}"] = (close - low_ma) / (high_ma - low_ma + 1e-10)
        feature_idx += 1
        features[f"feature_{feature_idx}"] = (high - close) / (high - low + 1e-10)
        feature_idx += 1
        features[f"feature_{feature_idx}"] = (close - low) / (high - low + 1e-10)
        feature_idx += 1
        features[f"feature_{feature_idx}"] = (high - low) / open_
        feature_idx += 1

        if feature_idx >= 140:
            break

    # 7. MACD-like features (10 features)
    ema_12 = close.ewm(span=12).mean()
    ema_26 = close.ewm(span=26).mean()
    macd = ema_12 - ema_26
    signal = macd.ewm(span=9).mean()
    features[f"feature_{feature_idx}"] = macd
    feature_idx += 1
    features[f"feature_{feature_idx}"] = signal
    feature_idx += 1
    features[f"feature_{feature_idx}"] = macd - signal
    feature_idx += 1
    features[f"feature_{feature_idx}"] = macd / close
    feature_idx += 1
    features[f"feature_{feature_idx}"] = signal / close
    feature_idx += 1

    # Fill remaining features with variations
    while feature_idx < 158:
        # Use random combinations of existing features
        if feature_idx % 2 == 0:
            features[f"feature_{feature_idx}"] = (
                close.pct_change().rolling(5 + feature_idx % 10).mean()
            )
        else:
            features[f"feature_{feature_idx}"] = (
                volume.pct_change().rolling(5 + feature_idx % 10).std()
            )
        feature_idx += 1

    # Forward fill NaN values
    features = features.ffill()

    # Backward fill remaining NaN (at start)
    features = features.bfill()

    # Fill any remaining NaN with 0
    features = features.fillna(0)

    # Replace inf with 0
    features = features.replace([np.inf, -np.inf], 0)

    return cast(pd.DataFrame, features)
