"""HAR (Heterogeneous Autoregressive) volatility forecasting model.

Implements the HAR-RV model from Corsi (2009) for volatility forecasting:
RV_{t+h} = c + b_d * RV_{t-1} + b_w * RV_w + b_m * RV_m + e

Where:
- RV_{t-1} = daily realized volatility (lag-1)
- RV_w = average RV over lags 1-5 (weekly)
- RV_m = average RV over lags 1-22 (monthly)
- h = forecast horizon (default: 1 day)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

import numpy as np
import polars as pl

logger = logging.getLogger(__name__)


@dataclass
class HARModelResult:
    """Result of HAR model fitting."""

    intercept: float
    coef_daily: float
    coef_weekly: float
    coef_monthly: float
    r_squared: float
    n_observations: int
    dataset_version_id: str
    fit_timestamp: datetime
    forecast_horizon: int


@dataclass
class HARForecastResult:
    """Result of HAR forecast."""

    forecast_date: date
    rv_forecast: float
    rv_forecast_annualized: float
    model_r_squared: float
    dataset_version_id: str


class HARVolatilityModel:
    """HAR-RV volatility forecasting model.

    Estimator: Ordinary Least Squares (OLS) via numpy.linalg.lstsq.
    - Simple, deterministic, numerically stable
    - No robust SE needed for forecasting (only point estimates)
    """

    def __init__(self, forecast_horizon: int = 1) -> None:
        """Initialize HAR model.

        Args:
            forecast_horizon: Number of days ahead to forecast (default: 1).
        """
        self.horizon = forecast_horizon
        self._fitted = False
        self._coefficients: np.ndarray[Any, np.dtype[np.floating[Any]]] | None = None
        self._r_squared: float | None = None
        self._dataset_version_id: str | None = None
        self._fit_timestamp: datetime | None = None
        self._n_observations: int | None = None

    def fit(
        self,
        realized_vol: pl.DataFrame,
        dataset_version_id: str,
    ) -> HARModelResult:
        """Fit HAR model using OLS.

        Args:
            realized_vol: DataFrame with 'date' and 'rv' columns.
                - Must be sorted by date ascending
                - Must have at least 60 observations
                - RV values should be non-annualized daily RV
            dataset_version_id: Version ID from source RV data.

        Returns:
            HARModelResult with coefficients, R², and metadata.

        Raises:
            ValueError: <60 days, non-monotonic dates, >5 consecutive NaNs,
                        or negative RV values.
        """
        if realized_vol.height < 60:
            raise ValueError(
                f"Minimum 60 observations required, got {realized_vol.height}"
            )

        dates = realized_vol["date"].to_list()
        for i in range(1, len(dates)):
            if dates[i] <= dates[i - 1]:
                raise ValueError("Dates must be monotonically increasing")

        rv_values = realized_vol["rv"].to_numpy()

        if np.any(rv_values < 0):
            raise ValueError("RV must be non-negative")

        rv_filled = self._forward_fill_nan(rv_values, max_consecutive=5)

        features_df = self._construct_har_features(rv_filled)

        mask = ~np.isnan(features_df["rv_target"])
        X = np.column_stack([
            np.ones(sum(mask)),
            features_df["rv_d"][mask],
            features_df["rv_w"][mask],
            features_df["rv_m"][mask],
        ])
        y = features_df["rv_target"][mask]

        coeffs, residuals, rank, s = np.linalg.lstsq(X, y, rcond=None)

        y_pred = X @ coeffs
        ss_res = np.sum((y - y_pred) ** 2)
        ss_tot = np.sum((y - np.mean(y)) ** 2)
        r_squared = 1 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

        self._coefficients = coeffs
        self._r_squared = float(r_squared)
        self._dataset_version_id = dataset_version_id
        self._fit_timestamp = datetime.now(UTC)
        self._n_observations = len(y)
        self._fitted = True

        if np.all(rv_values == 0):
            logger.warning(
                "All-zero RV values detected",
                extra={"dataset_version_id": dataset_version_id},
            )

        return HARModelResult(
            intercept=float(coeffs[0]),
            coef_daily=float(coeffs[1]),
            coef_weekly=float(coeffs[2]),
            coef_monthly=float(coeffs[3]),
            r_squared=self._r_squared,
            n_observations=self._n_observations,
            dataset_version_id=dataset_version_id,
            fit_timestamp=self._fit_timestamp,
            forecast_horizon=self.horizon,
        )

    def forecast(self, current_rv: pl.DataFrame) -> HARForecastResult:
        """Generate h-day ahead forecast.

        Args:
            current_rv: DataFrame with recent RV data.
                - Must have at least 22 rows for monthly lag
                - Latest date is t, forecast is for t+h

        Returns:
            HARForecastResult with point forecast.

        Raises:
            RuntimeError: If called before fit().
            ValueError: If insufficient data for lags.
        """
        if not self._fitted:
            raise RuntimeError("Model not fitted. Call fit() first.")

        if current_rv.height < 22:
            raise ValueError(
                f"Need at least 22 rows for monthly lag, got {current_rv.height}"
            )

        rv_values = current_rv["rv"].to_numpy()
        rv_values = self._forward_fill_nan(rv_values, max_consecutive=5)

        rv_d = rv_values[-1]
        rv_w = np.mean(rv_values[-5:])
        rv_m = np.mean(rv_values[-22:])

        X = np.array([1.0, rv_d, rv_w, rv_m])
        assert self._coefficients is not None
        rv_forecast = float(np.dot(self._coefficients, X))

        rv_forecast = max(0.0, rv_forecast)

        latest_date = current_rv["date"].to_list()[-1]
        forecast_date = latest_date

        assert self._r_squared is not None
        assert self._dataset_version_id is not None
        return HARForecastResult(
            forecast_date=forecast_date,
            rv_forecast=rv_forecast,
            rv_forecast_annualized=rv_forecast * math.sqrt(252),
            model_r_squared=self._r_squared,
            dataset_version_id=self._dataset_version_id,
        )

    def _forward_fill_nan(
        self, arr: np.ndarray[Any, np.dtype[np.floating[Any]]], max_consecutive: int = 5
    ) -> np.ndarray[Any, np.dtype[np.floating[Any]]]:
        """Forward-fill NaN values up to max_consecutive.

        Args:
            arr: Input array.
            max_consecutive: Maximum consecutive NaNs to fill.

        Returns:
            Array with NaNs forward-filled.

        Raises:
            ValueError: If more than max_consecutive consecutive NaNs.
        """
        result = arr.copy()
        consecutive_nan = 0
        last_valid = None

        for i in range(len(result)):
            if np.isnan(result[i]):
                consecutive_nan += 1
                if consecutive_nan > max_consecutive:
                    raise ValueError(
                        f"More than {max_consecutive} consecutive NaN values"
                    )
                if last_valid is not None:
                    result[i] = last_valid
            else:
                consecutive_nan = 0
                last_valid = result[i]

        return result

    def _construct_har_features(self, rv_values: np.ndarray[Any, np.dtype[np.floating[Any]]]) -> dict[str, np.ndarray[Any, np.dtype[np.floating[Any]]]]:
        """Construct HAR features from RV series.

        All lags exclude RV_t to prevent look-ahead bias:
        - rv_d: RV_{t-1} (lag-1 daily RV)
        - rv_w: mean(RV_{t-5}, ..., RV_{t-1}) (5-day average of lags 1-5)
        - rv_m: mean(RV_{t-22}, ..., RV_{t-1}) (22-day average of lags 1-22)

        Args:
            rv_values: Array of RV values.

        Returns:
            Dictionary with feature arrays and target.
        """
        n = len(rv_values)

        rv_d = np.full(n, np.nan)
        rv_w = np.full(n, np.nan)
        rv_m = np.full(n, np.nan)
        rv_target = np.full(n, np.nan)

        for t in range(22, n - self.horizon):
            rv_d[t] = rv_values[t - 1]

            rv_w[t] = np.mean(rv_values[t - 5 : t])

            rv_m[t] = np.mean(rv_values[t - 22 : t])

            rv_target[t] = rv_values[t + self.horizon]

        return {
            "rv_d": rv_d,
            "rv_w": rv_w,
            "rv_m": rv_m,
            "rv_target": rv_target,
        }
