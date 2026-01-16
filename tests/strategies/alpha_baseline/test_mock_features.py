"""
Unit tests for strategies.alpha_baseline.mock_features.

Tests cover:
- Mock Alpha158 feature generation from T1 data
- Date range handling (start/end dates, lookback periods)
- Multi-symbol data loading and processing
- Simple technical feature computation
- Feature shape and structure (158 features, MultiIndex)
- Error handling (missing files, invalid dates, empty data)
- Edge cases (None, empty lists, invalid inputs)
- NaN/inf handling in features

Target: 85%+ branch coverage (baseline from 0%)
"""

import shutil
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
import polars as pl
import pytest

from strategies.alpha_baseline.mock_features import (
    compute_simple_features,
    get_mock_alpha158_features,
)


class TestGetMockAlpha158Features:
    """Tests for get_mock_alpha158_features() function."""

    def setup_method(self) -> None:
        """Create temporary test data directory."""
        self.temp_dir = Path(tempfile.mkdtemp())

    def teardown_method(self) -> None:
        """Clean up temporary directory."""
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def _create_test_parquet(
        self,
        symbol: str,
        start_date: date,
        num_days: int,
        base_price: float = 100.0,
    ) -> None:
        """
        Helper to create test Parquet files with OHLCV data.

        Args:
            symbol: Stock symbol
            start_date: Starting date for data
            num_days: Number of days of data to create
            base_price: Base price for generating OHLCV
        """
        # Create partition directory
        partition_dir = self.temp_dir / start_date.strftime("%Y-%m-%d")
        partition_dir.mkdir(parents=True, exist_ok=True)

        # Generate date range
        dates = [start_date + timedelta(days=i) for i in range(num_days)]

        # Generate OHLCV data with some variation
        closes = [base_price * (1 + 0.01 * np.sin(i / 5)) for i in range(num_days)]
        opens = [c * 0.99 for c in closes]
        highs = [c * 1.02 for c in closes]
        lows = [c * 0.98 for c in closes]
        volumes = [1_000_000.0 + i * 10_000 for i in range(num_days)]

        # Create DataFrame
        df = pl.DataFrame(
            {
                "symbol": [symbol] * num_days,
                "date": dates,
                "open": opens,
                "high": highs,
                "low": lows,
                "close": closes,
                "volume": volumes,
            }
        )

        # Write to Parquet
        df.write_parquet(partition_dir / f"{symbol}.parquet")

    def test_get_mock_alpha158_features_single_symbol_success(self) -> None:
        """Test successful feature generation for single symbol."""
        # Create 90 days of data (60 for lookback + 30 for target range)
        start_date = date(2024, 1, 1)
        self._create_test_parquet("AAPL", start_date, 90, base_price=150.0)

        # Request features for last 30 days
        target_start = date(2024, 3, 1)
        target_end = date(2024, 3, 30)

        features = get_mock_alpha158_features(
            symbols=["AAPL"],
            start_date=target_start.strftime("%Y-%m-%d"),
            end_date=target_end.strftime("%Y-%m-%d"),
            data_dir=self.temp_dir,
        )

        # Check structure
        assert isinstance(features, pd.DataFrame)
        assert isinstance(features.index, pd.MultiIndex)
        assert features.index.names == ["datetime", "instrument"]

        # Check 158 features
        assert features.shape[1] == 158
        assert all(col.startswith("feature_") for col in features.columns)

        # Check we have data for AAPL
        assert "AAPL" in features.index.get_level_values("instrument").unique()

    def test_get_mock_alpha158_features_multiple_symbols(self) -> None:
        """Test feature generation for multiple symbols."""
        start_date = date(2024, 1, 1)
        self._create_test_parquet("AAPL", start_date, 90, base_price=150.0)
        self._create_test_parquet("MSFT", start_date, 90, base_price=350.0)
        self._create_test_parquet("GOOGL", start_date, 90, base_price=140.0)

        target_start = date(2024, 3, 1)
        target_end = date(2024, 3, 10)

        features = get_mock_alpha158_features(
            symbols=["AAPL", "MSFT", "GOOGL"],
            start_date=target_start.strftime("%Y-%m-%d"),
            end_date=target_end.strftime("%Y-%m-%d"),
            data_dir=self.temp_dir,
        )

        # Check all symbols present
        symbols = features.index.get_level_values("instrument").unique()
        assert set(symbols) == {"AAPL", "MSFT", "GOOGL"}

        # Check shape (10 days × 3 symbols × 158 features)
        assert features.shape[1] == 158
        assert len(features) == 10 * 3  # 10 days, 3 symbols

    def test_get_mock_alpha158_features_single_day(self) -> None:
        """Test feature generation for single day."""
        start_date = date(2024, 1, 1)
        self._create_test_parquet("AAPL", start_date, 90)

        target_date = date(2024, 3, 1)

        features = get_mock_alpha158_features(
            symbols=["AAPL"],
            start_date=target_date.strftime("%Y-%m-%d"),
            end_date=target_date.strftime("%Y-%m-%d"),
            data_dir=self.temp_dir,
        )

        # Check single day
        assert len(features) == 1
        assert features.index.get_level_values("datetime")[0].date() == target_date

    def test_get_mock_alpha158_features_no_parquet_file_raises_error(self) -> None:
        """Test error when no parquet file exists for symbol."""
        with pytest.raises(FileNotFoundError, match="No data found for symbol: AAPL"):
            get_mock_alpha158_features(
                symbols=["AAPL"],
                start_date="2024-01-01",
                end_date="2024-01-31",
                data_dir=self.temp_dir,
            )

    def test_get_mock_alpha158_features_no_data_in_date_range_raises_error(self) -> None:
        """Test error when parquet exists but has no data in date range."""
        # Create data for Jan 2024
        start_date = date(2024, 1, 1)
        self._create_test_parquet("AAPL", start_date, 30)

        # Request data for Dec 2025 (way out of range)
        with pytest.raises(
            FileNotFoundError,
            match="No data found for symbol AAPL in date range",
        ):
            get_mock_alpha158_features(
                symbols=["AAPL"],
                start_date="2025-12-01",
                end_date="2025-12-31",
                data_dir=self.temp_dir,
            )

    def test_get_mock_alpha158_features_empty_symbols_list(self) -> None:
        """Test error when no symbols provided."""
        start_date = date(2024, 1, 1)
        self._create_test_parquet("AAPL", start_date, 90)

        with pytest.raises(ValueError, match="No data loaded for any symbols"):
            get_mock_alpha158_features(
                symbols=[],
                start_date="2024-03-01",
                end_date="2024-03-31",
                data_dir=self.temp_dir,
            )

    def test_get_mock_alpha158_features_partial_symbol_coverage(self) -> None:
        """Test when some symbols have data but others don't."""
        # Only create data for AAPL, not MSFT
        start_date = date(2024, 1, 1)
        self._create_test_parquet("AAPL", start_date, 90)

        # Request both symbols - should fail on MSFT
        with pytest.raises(FileNotFoundError, match="No data found for symbol: MSFT"):
            get_mock_alpha158_features(
                symbols=["AAPL", "MSFT"],
                start_date="2024-03-01",
                end_date="2024-03-10",
                data_dir=self.temp_dir,
            )

    def test_get_mock_alpha158_features_lookback_period_calculation(self) -> None:
        """Test that lookback period (60 days) is correctly applied."""
        # Create exactly 70 days of data
        start_date = date(2024, 1, 1)
        self._create_test_parquet("AAPL", start_date, 70)

        # Request features for day 70 (need 60 days lookback = day 10-70)
        target_date = (start_date + timedelta(days=69)).strftime("%Y-%m-%d")

        features = get_mock_alpha158_features(
            symbols=["AAPL"],
            start_date=target_date,
            end_date=target_date,
            data_dir=self.temp_dir,
        )

        # Should succeed with enough lookback
        assert len(features) == 1

    def test_get_mock_alpha158_features_multiple_parquet_files_uses_latest(self) -> None:
        """Test that when multiple parquet files exist, it uses one with data."""
        # Create two partition directories
        partition1 = self.temp_dir / "2024-01-01"
        partition2 = self.temp_dir / "2024-02-01"
        partition1.mkdir(parents=True, exist_ok=True)
        partition2.mkdir(parents=True, exist_ok=True)

        # Create data in second partition only
        start_date = date(2024, 2, 1)
        dates = [start_date + timedelta(days=i) for i in range(90)]
        closes = [150.0] * 90

        df = pl.DataFrame(
            {
                "symbol": ["AAPL"] * 90,
                "date": dates,
                "open": [c * 0.99 for c in closes],
                "high": [c * 1.02 for c in closes],
                "low": [c * 0.98 for c in closes],
                "close": closes,
                "volume": [1_000_000.0] * 90,
            }
        )
        df.write_parquet(partition2 / "AAPL.parquet")

        # Create empty file in first partition
        pl.DataFrame(
            {
                "symbol": [],
                "date": [],
                "open": [],
                "high": [],
                "low": [],
                "close": [],
                "volume": [],
            }
        ).write_parquet(partition1 / "AAPL.parquet")

        # Should find data in second partition
        features = get_mock_alpha158_features(
            symbols=["AAPL"],
            start_date="2024-04-01",
            end_date="2024-04-10",
            data_dir=self.temp_dir,
        )

        assert len(features) > 0

    def test_get_mock_alpha158_features_invalid_date_format_raises_error(self) -> None:
        """Test error with invalid date format."""
        start_date = date(2024, 1, 1)
        self._create_test_parquet("AAPL", start_date, 90)

        with pytest.raises(ValueError):
            get_mock_alpha158_features(
                symbols=["AAPL"],
                start_date="invalid-date",
                end_date="2024-03-31",
                data_dir=self.temp_dir,
            )

    def test_get_mock_alpha158_features_end_date_before_start_date(self) -> None:
        """Test with end_date before start_date."""
        start_date = date(2024, 1, 1)
        self._create_test_parquet("AAPL", start_date, 90)

        # End before start - returns empty result
        features = get_mock_alpha158_features(
            symbols=["AAPL"],
            start_date="2024-03-31",
            end_date="2024-03-01",
            data_dir=self.temp_dir,
        )

        # Should return empty DataFrame (no rows match inverted date range)
        assert len(features) == 0

    def test_get_mock_alpha158_features_no_features_after_filtering(self) -> None:
        """Test when no features remain after date filtering."""
        # Create minimal data (5 days only)
        start_date = date(2024, 1, 1)
        self._create_test_parquet("AAPL", start_date, 5)

        # Request features - should work but may have limited data
        features = get_mock_alpha158_features(
            symbols=["AAPL"],
            start_date="2024-01-01",
            end_date="2024-01-05",
            data_dir=self.temp_dir,
        )

        # Should return features (even with limited data due to ffill/bfill)
        assert features.shape[1] == 158
        assert len(features) == 5

    def test_get_mock_alpha158_features_nonexistent_data_dir(self) -> None:
        """Test with non-existent data directory."""
        nonexistent_dir = Path("/nonexistent/path/to/data")

        # Should raise FileNotFoundError when searching for files
        with pytest.raises(FileNotFoundError):
            get_mock_alpha158_features(
                symbols=["AAPL"],
                start_date="2024-01-01",
                end_date="2024-01-31",
                data_dir=nonexistent_dir,
            )

    def test_get_mock_alpha158_features_feature_columns_only(self) -> None:
        """Test that only feature columns are returned (no OHLCV)."""
        start_date = date(2024, 1, 1)
        self._create_test_parquet("AAPL", start_date, 90)

        features = get_mock_alpha158_features(
            symbols=["AAPL"],
            start_date="2024-03-01",
            end_date="2024-03-10",
            data_dir=self.temp_dir,
        )

        # Check no OHLCV columns
        assert "open" not in features.columns
        assert "high" not in features.columns
        assert "low" not in features.columns
        assert "close" not in features.columns
        assert "volume" not in features.columns

        # Only feature columns
        assert all(col.startswith("feature_") for col in features.columns)


class TestComputeSimpleFeatures:
    """Tests for compute_simple_features() function."""

    def _create_test_dataframe(self, num_days: int = 100, base_price: float = 100.0) -> pd.DataFrame:
        """
        Helper to create test DataFrame with OHLCV data.

        Args:
            num_days: Number of days of data
            base_price: Base price for generating OHLCV

        Returns:
            DataFrame with date, symbol, OHLCV columns
        """
        dates = [date(2024, 1, 1) + timedelta(days=i) for i in range(num_days)]
        closes = [base_price * (1 + 0.01 * np.sin(i / 5)) for i in range(num_days)]

        return pd.DataFrame(
            {
                "date": dates,
                "symbol": ["AAPL"] * num_days,
                "open": [c * 0.99 for c in closes],
                "high": [c * 1.02 for c in closes],
                "low": [c * 0.98 for c in closes],
                "close": closes,
                "volume": [1_000_000.0 + i * 10_000 for i in range(num_days)],
            }
        )

    def test_compute_simple_features_returns_158_features(self) -> None:
        """Test that exactly 158 features are computed."""
        df = self._create_test_dataframe(num_days=100)

        features = compute_simple_features(df)

        # Check 158 features
        assert features.shape[1] == 158
        assert all(col.startswith("feature_") for col in features.columns)

        # Check feature naming (feature_0 to feature_157)
        expected_cols = [f"feature_{i}" for i in range(158)]
        assert list(features.columns) == expected_cols

    def test_compute_simple_features_index_is_datetime(self) -> None:
        """Test that index is datetime."""
        df = self._create_test_dataframe(num_days=100)

        features = compute_simple_features(df)

        # Check index is DatetimeIndex
        assert isinstance(features.index, pd.DatetimeIndex)

    def test_compute_simple_features_no_nan_values(self) -> None:
        """Test that NaN values are filled."""
        df = self._create_test_dataframe(num_days=100)

        features = compute_simple_features(df)

        # Check no NaN values
        assert not features.isna().any().any()

    def test_compute_simple_features_no_inf_values(self) -> None:
        """Test that inf values are replaced with 0."""
        df = self._create_test_dataframe(num_days=100)

        features = compute_simple_features(df)

        # Check no inf values
        assert not np.isinf(features.values).any()

    def test_compute_simple_features_with_zero_prices(self) -> None:
        """Test handling of zero prices (division by zero)."""
        df = self._create_test_dataframe(num_days=100)
        # Set some prices to zero
        df.loc[50:55, "close"] = 0.0
        df.loc[50:55, "high"] = 0.0
        df.loc[50:55, "low"] = 0.0
        df.loc[50:55, "open"] = 0.0

        features = compute_simple_features(df)

        # Should not raise error and should have no inf
        assert not np.isinf(features.values).any()
        assert not features.isna().any().any()

    def test_compute_simple_features_with_zero_volume(self) -> None:
        """Test handling of zero volume."""
        df = self._create_test_dataframe(num_days=100)
        df.loc[50:55, "volume"] = 0.0

        features = compute_simple_features(df)

        # Should not raise error
        assert not np.isinf(features.values).any()
        assert not features.isna().any().any()

    def test_compute_simple_features_minimal_data(self) -> None:
        """Test with minimal data (less than longest window)."""
        df = self._create_test_dataframe(num_days=10)

        features = compute_simple_features(df)

        # Should still return 158 features, with NaN filled
        assert features.shape[1] == 158
        assert len(features) == 10
        assert not features.isna().any().any()

    def test_compute_simple_features_single_row(self) -> None:
        """Test with single row of data."""
        df = self._create_test_dataframe(num_days=1)

        features = compute_simple_features(df)

        # Should return 158 features
        assert features.shape[1] == 158
        assert len(features) == 1
        assert not features.isna().any().any()

    def test_compute_simple_features_returns_feature_categories(self) -> None:
        """Test that different feature categories are computed."""
        df = self._create_test_dataframe(num_days=100)

        features = compute_simple_features(df)

        # Check features are diverse (not all zeros)
        # Features should have different values
        feature_stds = features.std()
        non_zero_features = (feature_stds > 0).sum()

        # Most features should have non-zero std (at least 100 out of 158)
        assert non_zero_features >= 100

    def test_compute_simple_features_with_constant_prices(self) -> None:
        """Test with constant prices (no volatility)."""
        df = self._create_test_dataframe(num_days=100)
        # Set constant prices
        df["close"] = 100.0
        df["open"] = 100.0
        df["high"] = 100.0
        df["low"] = 100.0

        features = compute_simple_features(df)

        # Should not raise error
        assert features.shape[1] == 158
        assert not features.isna().any().any()

    def test_compute_simple_features_with_missing_columns_raises_error(self) -> None:
        """Test error when required columns are missing."""
        df = pd.DataFrame(
            {
                "date": [date(2024, 1, 1)],
                "symbol": ["AAPL"],
                "close": [100.0],
                # Missing open, high, low, volume
            }
        )

        with pytest.raises(KeyError):
            compute_simple_features(df)

    def test_compute_simple_features_price_variations(self) -> None:
        """Test features with high price variations."""
        df = self._create_test_dataframe(num_days=100)
        # Add large price swings
        df.loc[::10, "close"] *= 1.5
        df.loc[1::10, "close"] *= 0.7

        features = compute_simple_features(df)

        # Should handle volatility
        assert features.shape[1] == 158
        assert not features.isna().any().any()
        assert not np.isinf(features.values).any()

    def test_compute_simple_features_forward_and_backward_fill(self) -> None:
        """Test that ffill and bfill are applied correctly."""
        df = self._create_test_dataframe(num_days=100)

        features = compute_simple_features(df)

        # With ffill and bfill, no NaN should remain
        assert not features.isna().any().any()

    def test_compute_simple_features_deterministic(self) -> None:
        """Test that features are deterministic (same input = same output)."""
        df = self._create_test_dataframe(num_days=100)

        features1 = compute_simple_features(df.copy())
        features2 = compute_simple_features(df.copy())

        # Should be identical
        pd.testing.assert_frame_equal(features1, features2)

    def test_compute_simple_features_rsi_calculation(self) -> None:
        """Test that RSI-like features are computed (feature indices ~70-100)."""
        df = self._create_test_dataframe(num_days=100)

        features = compute_simple_features(df)

        # RSI features should be in 0-100 range (or close after normalization)
        # Check they exist and are finite
        rsi_features = features[[f"feature_{i}" for i in range(70, 100)]]
        assert not rsi_features.isna().any().any()
        assert not np.isinf(rsi_features.values).any()

    def test_compute_simple_features_macd_calculation(self) -> None:
        """Test that MACD-like features are computed (feature indices ~140+)."""
        df = self._create_test_dataframe(num_days=100)

        features = compute_simple_features(df)

        # MACD features should exist
        macd_features = features[[f"feature_{i}" for i in range(140, 145)]]
        assert not macd_features.isna().any().any()
        assert not np.isinf(macd_features.values).any()

    def test_compute_simple_features_negative_prices(self) -> None:
        """Test handling of negative prices (edge case)."""
        df = self._create_test_dataframe(num_days=100)
        # Set some prices negative (shouldn't happen in real data)
        df.loc[50:55, "close"] = -100.0

        features = compute_simple_features(df)

        # Should handle gracefully
        assert features.shape[1] == 158
        assert not features.isna().any().any()

    def test_compute_simple_features_empty_dataframe(self) -> None:
        """Test with empty DataFrame."""
        df = pd.DataFrame(
            {
                "date": [],
                "symbol": [],
                "open": [],
                "high": [],
                "low": [],
                "close": [],
                "volume": [],
            }
        )

        # Empty DataFrame should return empty features
        features = compute_simple_features(df)

        # Should return empty with correct columns
        assert features.shape[1] == 158
        assert len(features) == 0
        assert all(col.startswith("feature_") for col in features.columns)
