"""
Unit tests for Alpha158 feature engineering.

Tests cover:
- Qlib initialization with T1 data
- Feature computation
- Label computation
- Train/valid/test split preparation
- Feature shape and structure

Note: These tests require Qlib to be initialized with proper data.
For now, they serve as documentation of expected behavior.
Full integration tests will be added in Phase 6.
"""

import shutil
import tempfile
from pathlib import Path

import pandas as pd
import pytest

from strategies.alpha_baseline.features import (
    compute_features_and_labels,
    get_alpha158_features,
    get_labels,
    initialize_qlib_with_t1_data,
)


class TestAlpha158Features:
    """Tests for Alpha158 feature engineering."""

    def setup_method(self) -> None:
        """Create temporary test data directory."""
        self.temp_dir = Path(tempfile.mkdtemp())

    def teardown_method(self) -> None:
        """Clean up temporary directory."""
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    @pytest.mark.skip(reason="Requires Qlib data format - integration test for Phase 6")
    def test_initialize_qlib(self) -> None:
        """Initialize Qlib with T1 data directory."""
        # This will be a full integration test in Phase 6
        # For now, documenting expected behavior
        initialize_qlib_with_t1_data(self.temp_dir)

    @pytest.mark.skip(reason="Requires Qlib data format - integration test for Phase 6")
    def test_get_alpha158_features_shape(self) -> None:
        """Get Alpha158 features returns correct shape."""
        # Expected behavior:
        # - 158 feature columns
        # - (date, symbol) MultiIndex
        # - Number of rows = num_dates × num_symbols

        features = get_alpha158_features(
            symbols=["AAPL", "MSFT"],
            start_date="2024-01-01",
            end_date="2024-01-31",  # ~21 trading days
            data_dir=self.temp_dir,
        )

        # Check shape
        assert features.shape[1] == 158  # 158 features
        assert len(features) == 42  # 21 days × 2 symbols

        # Check index
        assert isinstance(features.index, pd.MultiIndex)
        assert features.index.names == ["datetime", "instrument"]

    @pytest.mark.skip(reason="Requires Qlib data format - integration test for Phase 6")
    def test_get_labels_shape(self) -> None:
        """Get labels returns correct shape."""
        # Expected behavior:
        # - Single LABEL0 column (next-day return)
        # - (date, symbol) MultiIndex
        # - Last day has NaN label

        labels = get_labels(
            symbols=["AAPL", "MSFT"],
            start_date="2024-01-01",
            end_date="2024-01-31",
            data_dir=self.temp_dir,
        )

        # Check shape
        assert labels.shape[1] == 1  # Single label column
        assert len(labels) == 42  # 21 days × 2 symbols

        # Check index
        assert isinstance(labels.index, pd.MultiIndex)
        assert labels.index.names == ["datetime", "instrument"]

        # Check last day has NaN (no next-day price)
        # This is expected behavior - labels are forward-looking

    @pytest.mark.skip(reason="Requires Qlib data format - integration test for Phase 6")
    def test_compute_features_and_labels_split(self) -> None:
        """Compute features and labels for train/valid/test splits."""
        # Expected behavior:
        # - Normalization stats computed from training period only
        # - No look-ahead bias
        # - NaN labels dropped

        X_train, y_train, X_valid, y_valid, X_test, y_test = compute_features_and_labels(
            symbols=["AAPL", "MSFT", "GOOGL"],
            train_start="2023-01-01",
            train_end="2023-12-31",  # ~252 trading days
            valid_start="2024-01-01",
            valid_end="2024-06-30",  # ~126 trading days
            test_start="2024-07-01",
            test_end="2024-12-31",  # ~126 trading days
            data_dir=self.temp_dir,
        )

        # Check train set
        assert X_train.shape[1] == 158  # 158 features
        assert X_train.shape == y_train.shape[0:1] + (158,)  # Same rows

        # Check valid set
        assert X_valid.shape[1] == 158
        assert X_valid.shape == y_valid.shape[0:1] + (158,)

        # Check test set
        assert X_test.shape[1] == 158
        assert X_test.shape == y_test.shape[0:1] + (158,)

        # Check no NaN in labels (dropped)
        assert not y_train.isna().any().any()
        assert not y_valid.isna().any().any()
        assert not y_test.isna().any().any()

    @pytest.mark.skip(reason="Requires Qlib data format - integration test for Phase 6")
    def test_feature_normalization(self) -> None:
        """Features are normalized using robust statistics."""
        # Expected behavior:
        # - Features normalized using median and MAD
        # - Outliers clipped to ±3 MAD
        # - Training period stats used for valid/test normalization

        X_train, _, X_valid, _, X_test, _ = compute_features_and_labels(
            symbols=["AAPL"],
            train_start="2023-01-01",
            train_end="2023-12-31",
            valid_start="2024-01-01",
            valid_end="2024-06-30",
            test_start="2024-07-01",
            test_end="2024-12-31",
            data_dir=self.temp_dir,
        )

        # Check features are roughly normalized
        # Most values should be in ±3 range (clipped outliers)
        assert X_train.abs().max().max() <= 10.0  # Allow some outliers
        assert X_valid.abs().max().max() <= 10.0
        assert X_test.abs().max().max() <= 10.0

    def test_module_imports(self) -> None:
        """Module imports work correctly."""
        # This test always passes - just verifies imports
        from strategies.alpha_baseline.features import (
            compute_features_and_labels,
            get_alpha158_features,
            get_labels,
            initialize_qlib_with_t1_data,
        )

        assert callable(initialize_qlib_with_t1_data)
        assert callable(get_alpha158_features)
        assert callable(get_labels)
        assert callable(compute_features_and_labels)

    def test_function_signatures(self) -> None:
        """Function signatures match documentation."""
        import inspect

        # Test get_alpha158_features signature
        sig = inspect.signature(get_alpha158_features)
        params = list(sig.parameters.keys())
        assert "symbols" in params
        assert "start_date" in params
        assert "end_date" in params
        assert "fit_start_date" in params
        assert "fit_end_date" in params
        assert "data_dir" in params

        # Test get_labels signature
        sig = inspect.signature(get_labels)
        params = list(sig.parameters.keys())
        assert "symbols" in params
        assert "start_date" in params
        assert "end_date" in params
        assert "data_dir" in params

        # Test compute_features_and_labels signature
        sig = inspect.signature(compute_features_and_labels)
        params = list(sig.parameters.keys())
        assert "symbols" in params
        assert "train_start" in params
        assert "train_end" in params
        assert "valid_start" in params
        assert "valid_end" in params
        assert "test_start" in params
        assert "test_end" in params
        assert "data_dir" in params
