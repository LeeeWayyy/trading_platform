"""
Tests for shadow model validation.
"""

import numpy as np
import pandas as pd

from apps.signal_service.shadow_validator import ShadowModeValidator


class DummyModel:
    """Simple model stub with deterministic predictions."""

    def __init__(self, num_features: int, scale: float) -> None:
        self._num_features = num_features
        self._scale = scale

    def num_feature(self) -> int:
        return self._num_features

    def predict(self, features):
        return np.sum(features, axis=1) * self._scale


def _make_feature_frame() -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=5, freq="D")
    symbols = ["AAPL", "MSFT"]
    index = pd.MultiIndex.from_product([dates, symbols], names=["date", "instrument"])
    data = np.random.RandomState(0).rand(len(index), 3)
    return pd.DataFrame(data, index=index, columns=["f1", "f2", "f3"])


def _fake_feature_provider(features: pd.DataFrame):
    def _provider(symbols, start_date, end_date, data_dir=None):  # noqa: ARG001
        return features

    return _provider


def _setup_data_dir(temp_dir) -> None:
    (temp_dir / "2024-01-01").mkdir(parents=True, exist_ok=True)
    (temp_dir / "2024-01-02").mkdir(parents=True, exist_ok=True)


def test_shadow_validator_passes_for_similar_models(temp_dir):
    _setup_data_dir(temp_dir)
    features = _make_feature_frame()

    validator = ShadowModeValidator(
        data_dir=temp_dir,
        symbols=["AAPL", "MSFT"],
        sample_count=6,
        feature_provider=_fake_feature_provider(features),
    )

    old_model = DummyModel(num_features=3, scale=1.0)
    new_model = DummyModel(num_features=3, scale=1.05)

    result = validator.validate(old_model, new_model)

    assert result.passed is True
    assert result.correlation >= 0.5
    assert result.mean_abs_diff_ratio <= 0.5


def test_shadow_validator_rejects_low_correlation(temp_dir):
    _setup_data_dir(temp_dir)
    features = _make_feature_frame()

    validator = ShadowModeValidator(
        data_dir=temp_dir,
        symbols=["AAPL", "MSFT"],
        sample_count=6,
        feature_provider=_fake_feature_provider(features),
    )

    old_model = DummyModel(num_features=3, scale=1.0)
    new_model = DummyModel(num_features=3, scale=-1.0)

    result = validator.validate(old_model, new_model)

    assert result.passed is False
    assert result.correlation < 0.5


def test_shadow_validator_rejects_high_divergence(temp_dir):
    _setup_data_dir(temp_dir)
    features = _make_feature_frame()

    validator = ShadowModeValidator(
        data_dir=temp_dir,
        symbols=["AAPL", "MSFT"],
        sample_count=6,
        feature_provider=_fake_feature_provider(features),
    )

    old_model = DummyModel(num_features=3, scale=1.0)
    new_model = DummyModel(num_features=3, scale=3.0)

    result = validator.validate(old_model, new_model)

    assert result.passed is False
    assert result.mean_abs_diff_ratio > 0.5
