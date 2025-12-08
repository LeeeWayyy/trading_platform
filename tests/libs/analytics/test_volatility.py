"""Tests for HAR volatility forecasting model.

Tests cover:
- HAR Fitting (5 tests): Basic fitting, coefficients, R-squared, lag construction, version ID
- HAR Edge Cases (6 tests): Insufficient data, NaN handling, excessive NaN, non-monotonic, forecast before fit, negative RV
- HAR Forecast (4 tests): Positive forecasts, reasonable values, horizon parameter, insufficient data
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from libs.analytics.volatility import (
    HARForecastResult,
    HARModelResult,
    HARVolatilityModel,
)


def _create_rv_dataframe(n_days: int, base_rv: float = 0.01, seed: int = 42) -> pl.DataFrame:
    """Create synthetic RV data for testing.

    Args:
        n_days: Number of days of data.
        base_rv: Base realized volatility level.
        seed: Random seed for reproducibility.

    Returns:
        DataFrame with 'date' and 'rv' columns.
    """
    rng = np.random.default_rng(seed)
    start_date = date(2024, 1, 1)
    dates = [start_date + timedelta(days=i) for i in range(n_days)]
    rv_values = base_rv * (1 + 0.3 * rng.standard_normal(n_days))
    rv_values = np.abs(rv_values)
    return pl.DataFrame({"date": dates, "rv": rv_values})


def _create_ar1_rv_data(n_days: int, phi: float = 0.8, seed: int = 42) -> pl.DataFrame:
    """Create AR(1) RV data for testing HAR model.

    This generates data with known autocorrelation structure.

    Args:
        n_days: Number of days.
        phi: AR(1) coefficient.
        seed: Random seed.

    Returns:
        DataFrame with 'date' and 'rv' columns.
    """
    rng = np.random.default_rng(seed)
    start_date = date(2024, 1, 1)
    dates = [start_date + timedelta(days=i) for i in range(n_days)]

    rv = np.zeros(n_days)
    rv[0] = 0.01
    for i in range(1, n_days):
        rv[i] = phi * rv[i - 1] + 0.002 * rng.standard_normal()
    rv = np.abs(rv) + 0.001

    return pl.DataFrame({"date": dates, "rv": rv})


class TestHARFitting:
    """Tests for HAR model fitting."""

    def test_har_fit_basic(self) -> None:
        """Test basic HAR model fitting."""
        df = _create_rv_dataframe(100)
        model = HARVolatilityModel(forecast_horizon=1)

        result = model.fit(df, dataset_version_id="test_version_001")

        assert isinstance(result, HARModelResult)
        assert result.n_observations > 0
        assert result.forecast_horizon == 1
        assert result.fit_timestamp is not None

    def test_har_coefficients(self) -> None:
        """Test HAR coefficients are finite and reasonable."""
        df = _create_ar1_rv_data(100, phi=0.8)
        model = HARVolatilityModel()

        result = model.fit(df, dataset_version_id="test_version")

        assert math.isfinite(result.intercept)
        assert math.isfinite(result.coef_daily)
        assert math.isfinite(result.coef_weekly)
        assert math.isfinite(result.coef_monthly)
        assert abs(result.intercept) < 1.0
        assert abs(result.coef_daily) < 10.0
        assert abs(result.coef_weekly) < 10.0
        assert abs(result.coef_monthly) < 10.0

    def test_har_r_squared(self) -> None:
        """Test HAR R-squared is bounded [0, 1]."""
        df = _create_ar1_rv_data(100, phi=0.9)
        model = HARVolatilityModel()

        result = model.fit(df, dataset_version_id="test_version")

        assert 0.0 <= result.r_squared <= 1.0
        assert result.r_squared > 0.1

    def test_har_lag_construction(self) -> None:
        """Test HAR lags are constructed correctly without look-ahead bias."""
        df = _create_rv_dataframe(100, seed=123)
        model = HARVolatilityModel(forecast_horizon=1)
        rv_values = df["rv"].to_numpy()

        model.fit(df, dataset_version_id="test_version")
        features = model._construct_har_features(rv_values)

        t = 50
        assert features["rv_d"][t] == rv_values[t - 1]
        expected_rv_w = np.mean(rv_values[t - 5 : t])
        assert abs(features["rv_w"][t] - expected_rv_w) < 1e-10
        expected_rv_m = np.mean(rv_values[t - 22 : t])
        assert abs(features["rv_m"][t] - expected_rv_m) < 1e-10
        assert features["rv_target"][t] == rv_values[t + 1]

    def test_har_version_id_stored(self) -> None:
        """Test version ID is stored in model result."""
        df = _create_rv_dataframe(70)
        model = HARVolatilityModel()
        version_id = "composite_abc123def456"

        result = model.fit(df, dataset_version_id=version_id)

        assert result.dataset_version_id == version_id


class TestHAREdgeCases:
    """Tests for HAR model edge cases."""

    def test_har_insufficient_data(self) -> None:
        """Test HAR raises error with insufficient data (<60 days)."""
        df = _create_rv_dataframe(50)
        model = HARVolatilityModel()

        with pytest.raises(ValueError, match="Minimum 60 observations required"):
            model.fit(df, dataset_version_id="test")

    def test_har_nan_handling(self) -> None:
        """Test HAR handles sparse NaN values (<=5 consecutive)."""
        df = _create_rv_dataframe(80)
        rv_values = df["rv"].to_list()
        rv_values[30] = float("nan")
        rv_values[31] = float("nan")
        rv_values[40] = float("nan")
        df = pl.DataFrame({"date": df["date"], "rv": rv_values})

        model = HARVolatilityModel()
        result = model.fit(df, dataset_version_id="test")

        assert result.n_observations > 0
        assert 0.0 <= result.r_squared <= 1.0

    def test_har_excessive_nan(self) -> None:
        """Test HAR raises error with >5 consecutive NaN values."""
        df = _create_rv_dataframe(80)
        rv_values = df["rv"].to_list()
        for i in range(30, 37):
            rv_values[i] = float("nan")
        df = pl.DataFrame({"date": df["date"], "rv": rv_values})

        model = HARVolatilityModel()

        with pytest.raises(ValueError, match="More than 5 consecutive NaN values"):
            model.fit(df, dataset_version_id="test")

    def test_har_non_monotonic(self) -> None:
        """Test HAR raises error with non-monotonic dates."""
        start_date = date(2024, 1, 1)
        dates = [start_date + timedelta(days=i) for i in range(70)]
        dates[35], dates[36] = dates[36], dates[35]
        rv_values = [0.01] * 70

        df = pl.DataFrame({"date": dates, "rv": rv_values})
        model = HARVolatilityModel()

        with pytest.raises(ValueError, match="Dates must be monotonically increasing"):
            model.fit(df, dataset_version_id="test")

    def test_har_forecast_before_fit(self) -> None:
        """Test HAR raises error when forecasting before fit."""
        model = HARVolatilityModel()
        df = _create_rv_dataframe(30)

        with pytest.raises(RuntimeError, match="Model not fitted"):
            model.forecast(df)

    def test_har_negative_rv(self) -> None:
        """Test HAR raises error with negative RV values."""
        df = _create_rv_dataframe(70)
        rv_values = df["rv"].to_list()
        rv_values[35] = -0.01
        df = pl.DataFrame({"date": df["date"], "rv": rv_values})

        model = HARVolatilityModel()

        with pytest.raises(ValueError, match="RV must be non-negative"):
            model.fit(df, dataset_version_id="test")


class TestHARForecast:
    """Tests for HAR model forecasting."""

    def test_har_forecast_positive(self) -> None:
        """Test HAR forecasts are non-negative."""
        train_df = _create_ar1_rv_data(100)
        forecast_df = _create_rv_dataframe(30, seed=99)

        model = HARVolatilityModel()
        model.fit(train_df, dataset_version_id="test")

        result = model.forecast(forecast_df)

        assert result.rv_forecast >= 0.0
        assert result.rv_forecast_annualized >= 0.0

    def test_har_forecast_reasonable(self) -> None:
        """Test HAR forecast is within reasonable bounds."""
        train_df = _create_ar1_rv_data(100, phi=0.8)
        forecast_df = train_df.tail(30)

        model = HARVolatilityModel()
        model.fit(train_df, dataset_version_id="test")

        result = model.forecast(forecast_df)

        rv_mean = train_df["rv"].mean()
        assert result.rv_forecast > 0
        assert result.rv_forecast < rv_mean * 10

    def test_har_forecast_horizon(self) -> None:
        """Test HAR forecast with different horizons."""
        train_df = _create_ar1_rv_data(120)
        forecast_df = _create_rv_dataframe(30, seed=88)

        model_h1 = HARVolatilityModel(forecast_horizon=1)
        model_h5 = HARVolatilityModel(forecast_horizon=5)

        model_h1.fit(train_df, dataset_version_id="h1")
        model_h5.fit(train_df, dataset_version_id="h5")

        result_h1 = model_h1.forecast(forecast_df)
        result_h5 = model_h5.forecast(forecast_df)

        assert isinstance(result_h1, HARForecastResult)
        assert isinstance(result_h5, HARForecastResult)
        assert result_h1.rv_forecast >= 0
        assert result_h5.rv_forecast >= 0

    def test_har_forecast_insufficient_data(self) -> None:
        """Test HAR forecast raises error with insufficient recent data."""
        train_df = _create_ar1_rv_data(100)
        forecast_df = _create_rv_dataframe(15)

        model = HARVolatilityModel()
        model.fit(train_df, dataset_version_id="test")

        with pytest.raises(ValueError, match="Need at least 22 rows"):
            model.forecast(forecast_df)
