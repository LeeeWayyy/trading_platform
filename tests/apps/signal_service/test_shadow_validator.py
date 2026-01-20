"""
Comprehensive tests for shadow model validation.

Test Coverage:
- ShadowModeValidator initialization and configuration
- Validation logic (happy paths, error paths, edge cases)
- Feature loading and sampling
- Data directory parsing
- Correlation calculation edge cases
- Divergence calculation edge cases
- Model dimension mismatch handling
- Empty/insufficient data handling
"""

from unittest.mock import Mock

import numpy as np
import pandas as pd
import pytest

from apps.signal_service.shadow_validator import (
    ShadowModeValidator,
    ShadowValidationResult,
    _list_data_dates,
    _mean_abs_diff_ratio,
    _safe_correlation,
)

# ============================================================================
# Test Helpers and Fixtures
# ============================================================================


class DummyModel:
    """Simple model stub with deterministic predictions."""

    def __init__(self, num_features: int, scale: float, offset: float = 0.0) -> None:
        """
        Create dummy model.

        Args:
            num_features: Number of expected features
            scale: Multiplier for predictions
            offset: Additive offset for predictions
        """
        self._num_features = num_features
        self._scale = scale
        self._offset = offset

    def num_feature(self) -> int:
        """Return number of features."""
        return self._num_features

    def predict(self, features):
        """Generate predictions from features."""
        return np.sum(features, axis=1) * self._scale + self._offset


class ConstantModel:
    """Model that returns constant predictions."""

    def __init__(self, num_features: int, constant: float) -> None:
        """
        Create constant model.

        Args:
            num_features: Number of expected features
            constant: Constant value to return
        """
        self._num_features = num_features
        self._constant = constant

    def num_feature(self) -> int:
        """Return number of features."""
        return self._num_features

    def predict(self, features):
        """Generate constant predictions."""
        return np.full(features.shape[0], self._constant)


def _make_feature_frame(num_samples: int = 10, num_features: int = 3) -> pd.DataFrame:
    """
    Create mock feature DataFrame.

    Args:
        num_samples: Number of samples (date-symbol pairs)
        num_features: Number of feature columns

    Returns:
        DataFrame with (date, instrument) MultiIndex
    """
    dates = pd.date_range("2024-01-01", periods=num_samples // 2, freq="D")
    symbols = ["AAPL", "MSFT"]
    index = pd.MultiIndex.from_product([dates, symbols], names=["date", "instrument"])
    data = np.random.RandomState(0).rand(len(index), num_features)
    return pd.DataFrame(data, index=index, columns=[f"f{i}" for i in range(num_features)])


def _fake_feature_provider(features: pd.DataFrame):
    """
    Create fake feature provider function.

    Args:
        features: DataFrame to return

    Returns:
        Callable that returns the features
    """

    def _provider(symbols, start_date, end_date, data_dir=None):  # noqa: ARG001
        return features

    return _provider


def _setup_data_dir(temp_dir, dates: list[str] | None = None) -> None:
    """
    Create mock data directory structure.

    Args:
        temp_dir: Base directory
        dates: List of date strings to create (default: ["2024-01-01", "2024-01-02"])
    """
    if dates is None:
        dates = ["2024-01-01", "2024-01-02"]
    for date_str in dates:
        (temp_dir / date_str).mkdir(parents=True, exist_ok=True)


# ============================================================================
# ShadowModeValidator Initialization Tests
# ============================================================================


def test_validator_init_with_defaults(temp_dir):
    """Test validator initialization with default parameters."""
    _setup_data_dir(temp_dir)

    validator = ShadowModeValidator(
        data_dir=temp_dir,
        symbols=["AAPL", "MSFT"],
        sample_count=10,
    )

    assert validator.data_dir == temp_dir
    assert validator.symbols == ["AAPL", "MSFT"]
    assert validator.sample_count == 10
    assert validator.correlation_threshold == 0.5
    assert validator.divergence_threshold == 0.5
    assert validator.feature_provider is not None


def test_validator_init_with_custom_thresholds(temp_dir):
    """Test validator initialization with custom thresholds."""
    _setup_data_dir(temp_dir)

    validator = ShadowModeValidator(
        data_dir=temp_dir,
        symbols=["AAPL"],
        sample_count=5,
        correlation_threshold=0.7,
        divergence_threshold=0.3,
    )

    assert validator.correlation_threshold == 0.7
    assert validator.divergence_threshold == 0.3


def test_validator_init_normalizes_symbols(temp_dir):
    """Test that validator uppercases symbol names."""
    _setup_data_dir(temp_dir)

    validator = ShadowModeValidator(
        data_dir=temp_dir,
        symbols=["aapl", "msft", "GOOGL"],
        sample_count=10,
    )

    assert validator.symbols == ["AAPL", "MSFT", "GOOGL"]


def test_validator_init_with_custom_feature_provider(temp_dir):
    """Test validator with custom feature provider."""
    _setup_data_dir(temp_dir)
    mock_provider = Mock(return_value=pd.DataFrame())

    validator = ShadowModeValidator(
        data_dir=temp_dir,
        symbols=["AAPL"],
        sample_count=10,
        feature_provider=mock_provider,
    )

    assert validator.feature_provider is mock_provider


# ============================================================================
# Validation Happy Path Tests
# ============================================================================


def test_shadow_validator_passes_for_similar_models(temp_dir):
    """Test validation passes when models produce similar predictions."""
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
    assert result.sample_count == 6
    assert result.old_range > 0
    assert result.new_range > 0
    assert "passed" in result.message.lower()


def test_shadow_validator_rejects_low_correlation(temp_dir):
    """Test validation fails when correlation is too low."""
    _setup_data_dir(temp_dir)
    features = _make_feature_frame()

    validator = ShadowModeValidator(
        data_dir=temp_dir,
        symbols=["AAPL", "MSFT"],
        sample_count=6,
        feature_provider=_fake_feature_provider(features),
    )

    old_model = DummyModel(num_features=3, scale=1.0)
    new_model = DummyModel(num_features=3, scale=-1.0)  # Negative correlation

    result = validator.validate(old_model, new_model)

    assert result.passed is False
    assert result.correlation < 0.5
    assert "failed" in result.message.lower()


def test_shadow_validator_rejects_high_divergence(temp_dir):
    """Test validation fails when divergence is too high."""
    _setup_data_dir(temp_dir)
    features = _make_feature_frame()

    validator = ShadowModeValidator(
        data_dir=temp_dir,
        symbols=["AAPL", "MSFT"],
        sample_count=6,
        feature_provider=_fake_feature_provider(features),
    )

    old_model = DummyModel(num_features=3, scale=1.0)
    new_model = DummyModel(num_features=3, scale=3.0)  # High divergence

    result = validator.validate(old_model, new_model)

    assert result.passed is False
    assert result.mean_abs_diff_ratio > 0.5
    assert "failed" in result.message.lower()


def test_shadow_validator_computes_sign_change_rate(temp_dir):
    """Test that sign change rate is computed correctly."""
    _setup_data_dir(temp_dir)
    features = _make_feature_frame()

    validator = ShadowModeValidator(
        data_dir=temp_dir,
        symbols=["AAPL", "MSFT"],
        sample_count=6,
        feature_provider=_fake_feature_provider(features),
    )

    old_model = DummyModel(num_features=3, scale=1.0, offset=0.0)
    new_model = DummyModel(num_features=3, scale=-0.5, offset=0.0)

    result = validator.validate(old_model, new_model)

    # Models have opposite signs, so sign_change_rate should be high
    assert result.sign_change_rate > 0.9  # Most predictions flipped signs


def test_shadow_validator_edge_case_identical_models(temp_dir):
    """Test validation with identical models (perfect correlation)."""
    _setup_data_dir(temp_dir)
    features = _make_feature_frame()

    validator = ShadowModeValidator(
        data_dir=temp_dir,
        symbols=["AAPL", "MSFT"],
        sample_count=6,
        feature_provider=_fake_feature_provider(features),
    )

    model = DummyModel(num_features=3, scale=1.0)

    result = validator.validate(model, model)

    assert result.passed is True
    assert result.correlation >= 0.99  # Nearly perfect
    assert result.mean_abs_diff_ratio < 0.01  # Nearly zero
    assert result.sign_change_rate == 0.0  # No sign changes


# ============================================================================
# Validation Error Path Tests
# ============================================================================


def test_validation_fails_when_old_model_dimension_mismatch(temp_dir):
    """Test validation raises error when old model dimension doesn't match features."""
    _setup_data_dir(temp_dir)
    features = _make_feature_frame(num_features=3)

    validator = ShadowModeValidator(
        data_dir=temp_dir,
        symbols=["AAPL", "MSFT"],
        sample_count=6,
        feature_provider=_fake_feature_provider(features),
    )

    old_model = DummyModel(num_features=5, scale=1.0)  # Wrong dimension
    new_model = DummyModel(num_features=3, scale=1.0)

    with pytest.raises(ValueError, match="Feature dimension mismatch for old model"):
        validator.validate(old_model, new_model)


def test_validation_fails_when_new_model_dimension_mismatch(temp_dir):
    """Test validation raises error when new model dimension doesn't match features."""
    _setup_data_dir(temp_dir)
    features = _make_feature_frame(num_features=3)

    validator = ShadowModeValidator(
        data_dir=temp_dir,
        symbols=["AAPL", "MSFT"],
        sample_count=6,
        feature_provider=_fake_feature_provider(features),
    )

    old_model = DummyModel(num_features=3, scale=1.0)
    new_model = DummyModel(num_features=5, scale=1.0)  # Wrong dimension

    with pytest.raises(ValueError, match="Feature dimension mismatch for new model"):
        validator.validate(old_model, new_model)


def test_validation_fails_when_no_data_partitions(temp_dir):
    """Test validation raises error when no data partitions exist."""
    # Don't setup data dir - leave it empty

    validator = ShadowModeValidator(
        data_dir=temp_dir,
        symbols=["AAPL"],
        sample_count=10,
    )

    old_model = DummyModel(num_features=3, scale=1.0)
    new_model = DummyModel(num_features=3, scale=1.0)

    with pytest.raises(ValueError, match="No data partitions found"):
        validator.validate(old_model, new_model)


def test_validation_fails_when_features_empty(temp_dir):
    """Test validation raises error when feature provider returns empty DataFrame."""
    _setup_data_dir(temp_dir)

    validator = ShadowModeValidator(
        data_dir=temp_dir,
        symbols=["AAPL"],
        sample_count=10,
        feature_provider=_fake_feature_provider(pd.DataFrame()),  # Empty
    )

    old_model = DummyModel(num_features=3, scale=1.0)
    new_model = DummyModel(num_features=3, scale=1.0)

    with pytest.raises(ValueError, match="No features returned"):
        validator.validate(old_model, new_model)


def test_validation_fails_when_insufficient_samples(temp_dir):
    """Test validation raises error when not enough samples after filtering."""
    _setup_data_dir(temp_dir)
    features = _make_feature_frame(num_samples=4, num_features=3)

    validator = ShadowModeValidator(
        data_dir=temp_dir,
        symbols=["AAPL", "MSFT"],
        sample_count=10,  # Request more than available
        feature_provider=_fake_feature_provider(features),
    )

    old_model = DummyModel(num_features=3, scale=1.0)
    new_model = DummyModel(num_features=3, scale=1.0)

    with pytest.raises(ValueError, match="Not enough feature samples"):
        validator.validate(old_model, new_model)


# ============================================================================
# Feature Loading Tests
# ============================================================================


def test_load_feature_samples_with_multiple_dates(temp_dir):
    """Test feature loading with multiple date partitions."""
    _setup_data_dir(temp_dir, dates=["2024-01-01", "2024-01-02", "2024-01-03"])
    features = _make_feature_frame(num_samples=20, num_features=3)

    validator = ShadowModeValidator(
        data_dir=temp_dir,
        symbols=["AAPL", "MSFT"],
        sample_count=10,
        feature_provider=_fake_feature_provider(features),
    )

    loaded = validator._load_feature_samples()

    assert loaded.shape[0] == 10  # Requested sample count
    assert loaded.shape[1] == 3  # Number of features


def test_load_feature_samples_filters_na(temp_dir):
    """Test feature loading drops rows with NaN values."""
    _setup_data_dir(temp_dir)
    features = _make_feature_frame(num_samples=20, num_features=3)

    # Add NaN to some rows
    features.iloc[0, 0] = np.nan
    features.iloc[5, 1] = np.nan

    validator = ShadowModeValidator(
        data_dir=temp_dir,
        symbols=["AAPL", "MSFT"],
        sample_count=10,
        feature_provider=_fake_feature_provider(features),
    )

    loaded = validator._load_feature_samples()

    # Should have dropped rows with NaN
    assert not loaded.isna().any().any()
    assert loaded.shape[0] == 10


def test_load_feature_samples_returns_tail(temp_dir):
    """Test feature loading returns most recent samples."""
    _setup_data_dir(temp_dir)
    features = _make_feature_frame(num_samples=20, num_features=3)

    validator = ShadowModeValidator(
        data_dir=temp_dir,
        symbols=["AAPL", "MSFT"],
        sample_count=5,
        feature_provider=_fake_feature_provider(features),
    )

    loaded = validator._load_feature_samples()

    # Should return last 5 rows
    assert loaded.shape[0] == 5
    pd.testing.assert_frame_equal(loaded, features.tail(5))


# ============================================================================
# Date Parsing Tests
# ============================================================================


def test_list_data_dates_empty_directory(temp_dir):
    """Test _list_data_dates returns empty list for empty directory."""
    dates = _list_data_dates(temp_dir)
    assert dates == []


def test_list_data_dates_nonexistent_directory(temp_dir):
    """Test _list_data_dates returns empty list for nonexistent directory."""
    nonexistent = temp_dir / "nonexistent"
    dates = _list_data_dates(nonexistent)
    assert dates == []


def test_list_data_dates_with_valid_dates(temp_dir):
    """Test _list_data_dates parses valid date directories."""
    _setup_data_dir(temp_dir, dates=["2024-01-01", "2024-01-15", "2024-01-10"])

    dates = _list_data_dates(temp_dir)

    assert len(dates) == 3
    assert dates == sorted(dates)  # Should be sorted
    assert str(dates[0]) == "2024-01-01"
    assert str(dates[1]) == "2024-01-10"
    assert str(dates[2]) == "2024-01-15"


def test_list_data_dates_ignores_invalid_names(temp_dir):
    """Test _list_data_dates ignores non-date directories."""
    _setup_data_dir(temp_dir, dates=["2024-01-01", "2024-01-02"])
    (temp_dir / "not-a-date").mkdir()
    (temp_dir / "2024-13-99").mkdir()  # Invalid date
    (temp_dir / "file.txt").touch()  # File, not directory

    dates = _list_data_dates(temp_dir)

    assert len(dates) == 2
    assert str(dates[0]) == "2024-01-01"
    assert str(dates[1]) == "2024-01-02"


# ============================================================================
# Correlation Calculation Tests
# ============================================================================


def test_safe_correlation_normal_case():
    """Test _safe_correlation with normal arrays."""
    a = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    b = np.array([2.0, 4.0, 6.0, 8.0, 10.0])

    corr = _safe_correlation(a, b)

    assert abs(corr - 1.0) < 0.01  # Perfect positive correlation


def test_safe_correlation_negative():
    """Test _safe_correlation with negative correlation."""
    a = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    b = np.array([5.0, 4.0, 3.0, 2.0, 1.0])

    corr = _safe_correlation(a, b)

    assert abs(corr - (-1.0)) < 0.01  # Perfect negative correlation


def test_safe_correlation_constant_vectors():
    """Test _safe_correlation handles constant vectors."""
    a = np.array([5.0, 5.0, 5.0, 5.0])
    b = np.array([5.0, 5.0, 5.0, 5.0])

    corr = _safe_correlation(a, b)

    assert corr == 1.0  # Identical constants → correlation = 1.0


def test_safe_correlation_constant_different():
    """Test _safe_correlation with different constant vectors."""
    a = np.array([5.0, 5.0, 5.0, 5.0])
    b = np.array([3.0, 3.0, 3.0, 3.0])

    corr = _safe_correlation(a, b)

    assert corr == 0.0  # Different constants → correlation = 0.0


def test_safe_correlation_small_arrays():
    """Test _safe_correlation handles arrays with < 2 elements."""
    a = np.array([1.0])
    b = np.array([2.0])

    corr = _safe_correlation(a, b)

    assert corr == 1.0  # Too small → return 1.0


def test_safe_correlation_one_constant_one_varying():
    """Test _safe_correlation when one array is constant."""
    a = np.array([5.0, 5.0, 5.0, 5.0])
    b = np.array([1.0, 2.0, 3.0, 4.0])

    corr = _safe_correlation(a, b)

    assert corr == 0.0  # Constant vs varying → no correlation


# ============================================================================
# Divergence Calculation Tests
# ============================================================================


def test_mean_abs_diff_ratio_normal_case():
    """Test _mean_abs_diff_ratio with normal arrays."""
    a = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    b = np.array([1.1, 2.2, 3.3, 4.4, 5.5])

    ratio = _mean_abs_diff_ratio(a, b)

    # Mean abs diff = 0.3, mean abs baseline = 3.0, ratio = 0.1
    assert abs(ratio - 0.1) < 0.01


def test_mean_abs_diff_ratio_identical():
    """Test _mean_abs_diff_ratio with identical arrays."""
    a = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    b = np.array([1.0, 2.0, 3.0, 4.0, 5.0])

    ratio = _mean_abs_diff_ratio(a, b)

    assert ratio == 0.0  # No difference


def test_mean_abs_diff_ratio_zero_baseline():
    """Test _mean_abs_diff_ratio when baseline is zero."""
    a = np.array([0.0, 0.0, 0.0, 0.0])
    b = np.array([1.0, 2.0, 3.0, 4.0])

    ratio = _mean_abs_diff_ratio(a, b)

    # Baseline < 1e-12, so return mean abs diff directly
    assert abs(ratio - 2.5) < 0.01  # mean([1,2,3,4]) = 2.5


def test_mean_abs_diff_ratio_large_divergence():
    """Test _mean_abs_diff_ratio with large divergence."""
    a = np.array([1.0, 1.0, 1.0, 1.0])
    b = np.array([10.0, 10.0, 10.0, 10.0])

    ratio = _mean_abs_diff_ratio(a, b)

    # Mean abs diff = 9.0, baseline = 1.0, ratio = 9.0
    assert abs(ratio - 9.0) < 0.01


# ============================================================================
# ShadowValidationResult Tests
# ============================================================================


def test_shadow_validation_result_immutable():
    """Test that ShadowValidationResult is immutable (frozen dataclass)."""
    result = ShadowValidationResult(
        passed=True,
        correlation=0.95,
        mean_abs_diff_ratio=0.1,
        sign_change_rate=0.05,
        sample_count=100,
        old_range=5.0,
        new_range=4.8,
        message="test",
    )

    with pytest.raises(AttributeError):
        result.passed = False  # Should raise error (frozen)


def test_shadow_validation_result_contains_all_fields():
    """Test that ShadowValidationResult contains all expected fields."""
    result = ShadowValidationResult(
        passed=True,
        correlation=0.95,
        mean_abs_diff_ratio=0.1,
        sign_change_rate=0.05,
        sample_count=100,
        old_range=5.0,
        new_range=4.8,
        message="validation passed",
    )

    assert result.passed is True
    assert result.correlation == 0.95
    assert result.mean_abs_diff_ratio == 0.1
    assert result.sign_change_rate == 0.05
    assert result.sample_count == 100
    assert result.old_range == 5.0
    assert result.new_range == 4.8
    assert result.message == "validation passed"


# ============================================================================
# Integration Tests
# ============================================================================


def test_validator_integration_with_constant_models(temp_dir):
    """Integration test with constant prediction models."""
    _setup_data_dir(temp_dir)
    features = _make_feature_frame(num_samples=20, num_features=3)

    validator = ShadowModeValidator(
        data_dir=temp_dir,
        symbols=["AAPL", "MSFT"],
        sample_count=10,
        feature_provider=_fake_feature_provider(features),
    )

    old_model = ConstantModel(num_features=3, constant=5.0)
    new_model = ConstantModel(num_features=3, constant=5.0)

    result = validator.validate(old_model, new_model)

    assert result.passed is True
    assert result.correlation == 1.0  # Identical constants
    assert result.mean_abs_diff_ratio == 0.0
    assert result.sign_change_rate == 0.0


def test_validator_integration_with_offset_models(temp_dir):
    """Integration test with models having different offsets."""
    _setup_data_dir(temp_dir)
    features = _make_feature_frame(num_samples=20, num_features=3)

    validator = ShadowModeValidator(
        data_dir=temp_dir,
        symbols=["AAPL", "MSFT"],
        sample_count=10,
        correlation_threshold=0.9,  # Tighter threshold
        divergence_threshold=0.2,
        feature_provider=_fake_feature_provider(features),
    )

    old_model = DummyModel(num_features=3, scale=1.0, offset=0.0)
    new_model = DummyModel(num_features=3, scale=1.0, offset=0.1)  # Small offset

    result = validator.validate(old_model, new_model)

    # High correlation (same scale), low divergence (small offset)
    assert result.passed is True
    assert result.correlation > 0.99


@pytest.mark.parametrize(
    ("correlation_threshold", "divergence_threshold", "old_scale", "new_scale", "expected_pass"),
    [
        (0.5, 0.5, 1.0, 1.1, True),  # Similar models → pass
        (0.5, 0.5, 1.0, -1.0, False),  # Opposite signs → fail
        (0.5, 0.5, 1.0, 5.0, False),  # High divergence → fail
        (0.9, 0.1, 1.0, 1.01, True),  # Tight thresholds, nearly identical → pass
        (0.9, 0.1, 1.0, 1.5, False),  # Tight thresholds, moderate diff → fail
    ],
)
def test_validator_parametrized_thresholds(
    temp_dir,
    correlation_threshold,
    divergence_threshold,
    old_scale,
    new_scale,
    expected_pass,
):
    """Parametrized test for different threshold combinations."""
    _setup_data_dir(temp_dir)
    features = _make_feature_frame(num_samples=20, num_features=3)

    validator = ShadowModeValidator(
        data_dir=temp_dir,
        symbols=["AAPL", "MSFT"],
        sample_count=10,
        correlation_threshold=correlation_threshold,
        divergence_threshold=divergence_threshold,
        feature_provider=_fake_feature_provider(features),
    )

    old_model = DummyModel(num_features=3, scale=old_scale)
    new_model = DummyModel(num_features=3, scale=new_scale)

    result = validator.validate(old_model, new_model)

    assert result.passed == expected_pass
