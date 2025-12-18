"""
Shadow mode validation for model hot swaps.

This module validates a candidate model against the currently active model
using recent feature samples. It helps prevent corrupt or degenerate models
from going live immediately after a reload.
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt
import pandas as pd

from strategies.alpha_baseline.features import get_alpha158_features

FeatureProvider = Callable[..., pd.DataFrame]


@dataclass(frozen=True)
class ShadowValidationResult:
    """Result of shadow validation between old and new models."""

    passed: bool
    correlation: float
    mean_abs_diff_ratio: float
    sign_change_rate: float
    sample_count: int
    old_range: float
    new_range: float
    message: str


class ShadowModeValidator:
    """
    Validate a new model against the current model using recent features.

    Validation checks:
      - Correlation between predictions (>= correlation_threshold)
      - Mean absolute difference ratio (<= divergence_threshold)
      - Sign change rate is reported for visibility
    """

    def __init__(
        self,
        *,
        data_dir: Path,
        symbols: list[str],
        sample_count: int,
        correlation_threshold: float = 0.5,
        divergence_threshold: float = 0.5,
        feature_provider: FeatureProvider | None = None,
    ) -> None:
        self.data_dir = Path(data_dir)
        self.symbols = [s.upper() for s in symbols]
        self.sample_count = sample_count
        self.correlation_threshold = correlation_threshold
        self.divergence_threshold = divergence_threshold
        self.feature_provider = feature_provider or get_alpha158_features

    def validate(self, old_model: Any, new_model: Any) -> ShadowValidationResult:
        """Run shadow validation using recent feature samples."""
        features = self._load_feature_samples()
        sample_count = features.shape[0]

        old_features = features.values
        new_features = features.values

        if old_features.shape[1] != old_model.num_feature():
            raise ValueError(
                "Feature dimension mismatch for old model: "
                f"features={old_features.shape[1]}, model={old_model.num_feature()}"
            )
        if new_features.shape[1] != new_model.num_feature():
            raise ValueError(
                "Feature dimension mismatch for new model: "
                f"features={new_features.shape[1]}, model={new_model.num_feature()}"
            )

        old_pred = np.asarray(old_model.predict(old_features))
        new_pred = np.asarray(new_model.predict(new_features))

        correlation = _safe_correlation(old_pred, new_pred)
        mean_abs_diff_ratio = _mean_abs_diff_ratio(old_pred, new_pred)
        sign_change_rate = float(np.mean(old_pred * new_pred < 0))

        old_range = float(np.max(old_pred) - np.min(old_pred))
        new_range = float(np.max(new_pred) - np.min(new_pred))

        passed = (correlation >= self.correlation_threshold) and (
            mean_abs_diff_ratio <= self.divergence_threshold
        )

        message = (
            "shadow validation passed"
            if passed
            else "shadow validation failed: correlation or divergence threshold"
        )

        return ShadowValidationResult(
            passed=passed,
            correlation=correlation,
            mean_abs_diff_ratio=mean_abs_diff_ratio,
            sign_change_rate=sign_change_rate,
            sample_count=sample_count,
            old_range=old_range,
            new_range=new_range,
            message=message,
        )

    def _load_feature_samples(self) -> pd.DataFrame:
        """Load most recent feature samples for validation."""
        available_dates = _list_data_dates(self.data_dir)
        if not available_dates:
            raise ValueError(f"No data partitions found under {self.data_dir}")

        required_days = max(1, math.ceil(self.sample_count / max(1, len(self.symbols))))
        window_days = min(len(available_dates), max(60, required_days))
        start_date = available_dates[-window_days]
        end_date = available_dates[-1]

        features = self.feature_provider(
            self.symbols,
            start_date.isoformat(),
            end_date.isoformat(),
            data_dir=self.data_dir,
        )

        if features.empty:
            raise ValueError("No features returned for shadow validation")

        features = features.sort_index().dropna(how="any")
        if features.shape[0] < self.sample_count:
            raise ValueError(
                "Not enough feature samples for shadow validation: "
                f"needed={self.sample_count}, available={features.shape[0]}"
            )

        return features.tail(self.sample_count)


def _list_data_dates(data_dir: Path) -> list[date]:
    """Return sorted list of available YYYY-MM-DD partitions under data_dir."""
    dates: list[date] = []
    if not data_dir.exists():
        return dates

    for entry in data_dir.iterdir():
        if not entry.is_dir():
            continue
        try:
            dates.append(date.fromisoformat(entry.name))
        except ValueError:
            continue

    return sorted(dates)


def _safe_correlation(a: npt.NDArray[Any], b: npt.NDArray[Any]) -> float:
    """Compute correlation safely, handling constant vectors."""
    if a.size < 2 or b.size < 2:
        return 1.0

    a_std = float(np.std(a))
    b_std = float(np.std(b))
    if a_std < 1e-12 or b_std < 1e-12:
        return 1.0 if np.allclose(a, b) else 0.0

    return float(np.corrcoef(a, b)[0, 1])


def _mean_abs_diff_ratio(a: npt.NDArray[Any], b: npt.NDArray[Any]) -> float:
    """Compute mean absolute difference ratio against baseline a."""
    baseline = float(np.mean(np.abs(a)))
    if baseline < 1e-12:
        return float(np.mean(np.abs(a - b)))
    return float(np.mean(np.abs(a - b)) / baseline)
