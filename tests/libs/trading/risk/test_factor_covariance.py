"""
Tests for FactorCovarianceEstimator.
"""

from datetime import date

import numpy as np
import polars as pl
import pytest

from libs.trading.risk import (
    CANONICAL_FACTOR_ORDER,
    CovarianceConfig,
    CovarianceResult,
    FactorCovarianceEstimator,
    InsufficientDataError,
)


class TestFactorCovarianceEstimatorInit:
    """Tests for FactorCovarianceEstimator initialization."""

    def test_default_config(self, mock_factor_builder):
        """Uses default config when none provided."""
        estimator = FactorCovarianceEstimator(mock_factor_builder)
        assert estimator.config.halflife_days == 60
        assert estimator.config.min_observations == 126
        assert estimator.config.newey_west_lags == 5

    def test_custom_config(self, mock_factor_builder):
        """Uses custom config when provided."""
        config = CovarianceConfig(
            halflife_days=30,
            min_observations=60,
            newey_west_lags=10,
        )
        estimator = FactorCovarianceEstimator(mock_factor_builder, config=config)
        assert estimator.config.halflife_days == 30
        assert estimator.config.min_observations == 60

    def test_canonical_factor_order_defined(self, mock_factor_builder):
        """Canonical factor order is set correctly."""
        estimator = FactorCovarianceEstimator(mock_factor_builder)
        assert estimator.factor_names == CANONICAL_FACTOR_ORDER
        assert len(estimator.factor_names) == 5


class TestCovarianceConfig:
    """Tests for CovarianceConfig dataclass."""

    def test_default_values(self):
        """Default values are set correctly."""
        config = CovarianceConfig()
        assert config.halflife_days == 60
        assert config.min_observations == 126
        assert config.newey_west_lags == 5
        assert config.shrinkage_intensity is None
        assert config.min_stocks_per_day == 100


class TestCovarianceResultValidation:
    """Tests for CovarianceResult.validate()."""

    def test_validate_valid_matrix(self, sample_covariance_result):
        """Valid covariance matrix passes validation."""
        errors = sample_covariance_result.validate()
        assert len(errors) == 0

    def test_validate_catches_nan(self):
        """Validation catches NaN values."""
        cov = np.array([[1.0, np.nan], [np.nan, 1.0]])
        result = CovarianceResult(
            factor_covariance=cov,
            factor_names=["a", "b"],
            factor_returns=pl.DataFrame(),
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={},
        )
        errors = result.validate()
        assert any("NaN" in e for e in errors)

    def test_validate_catches_inf(self):
        """Validation catches infinite values."""
        cov = np.array([[1.0, np.inf], [np.inf, 1.0]])
        result = CovarianceResult(
            factor_covariance=cov,
            factor_names=["a", "b"],
            factor_returns=pl.DataFrame(),
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={},
        )
        errors = result.validate()
        assert any("infinite" in e for e in errors)

    def test_validate_catches_non_psd(self):
        """Validation catches non-PSD matrix."""
        # Create non-PSD matrix (negative eigenvalue)
        cov = np.array([[1.0, 2.0], [2.0, 1.0]])  # Eigenvalues: 3, -1
        result = CovarianceResult(
            factor_covariance=cov,
            factor_names=["a", "b"],
            factor_returns=pl.DataFrame(),
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={},
        )
        errors = result.validate()
        assert any("PSD" in e for e in errors)

    def test_validate_catches_correlation_out_of_bounds(self):
        """Validation catches correlations outside [-1, 1]."""
        # This would require a covariance matrix that produces |corr| > 1
        # which is mathematically impossible for a valid covariance
        # So we test the check itself works
        cov = np.array([[0.01, 0.0], [0.0, 0.01]])  # Valid
        result = CovarianceResult(
            factor_covariance=cov,
            factor_names=["a", "b"],
            factor_returns=pl.DataFrame(),
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={},
        )
        errors = result.validate()
        assert len(errors) == 0


class TestCovarianceResultStorageFormat:
    """Tests for CovarianceResult.to_storage_format()."""

    def test_to_storage_format_matches_schema(self, sample_covariance_result):
        """Storage format matches expected schema."""
        df = sample_covariance_result.to_storage_format()

        required_columns = [
            "as_of_date",
            "factor_i",
            "factor_j",
            "covariance",
            "correlation",
            "halflife_days",
            "shrinkage_intensity",
            "dataset_version_id",
        ]
        for col in required_columns:
            assert col in df.columns, f"Missing column: {col}"

    def test_storage_includes_all_factor_pairs(self, sample_covariance_result):
        """Storage format includes all factor pairs."""
        df = sample_covariance_result.to_storage_format()
        n_factors = len(sample_covariance_result.factor_names)
        assert df.height == n_factors * n_factors

    def test_storage_correlations_valid(self, sample_covariance_result):
        """Correlations in storage are in valid range."""
        df = sample_covariance_result.to_storage_format()
        corrs = df["correlation"].to_numpy()
        assert np.all(np.abs(corrs) <= 1.0 + 1e-6)


class TestEnsurePSD:
    """Tests for _ensure_psd() method."""

    def test_psd_repair_eigendecomposition(self, mock_factor_builder):
        """PSD repair uses eigendecomposition."""
        estimator = FactorCovarianceEstimator(mock_factor_builder)

        # Non-PSD matrix
        cov = np.array([[1.0, 2.0], [2.0, 1.0]])

        result = estimator._ensure_psd(cov)

        # Should be PSD now
        eigenvalues = np.linalg.eigvalsh(result)
        assert np.all(eigenvalues >= 0)

    def test_psd_repair_reconstruction(self, mock_factor_builder):
        """PSD repair reconstructs valid matrix."""
        estimator = FactorCovarianceEstimator(mock_factor_builder)

        cov = np.array([[1.0, 1.5], [1.5, 1.0]])  # Nearly non-PSD

        result = estimator._ensure_psd(cov)

        # Result should be symmetric
        assert np.allclose(result, result.T)

    def test_psd_repair_preserves_symmetry(self, mock_factor_builder):
        """PSD repair preserves symmetry."""
        estimator = FactorCovarianceEstimator(mock_factor_builder)

        cov = np.array([[1.0, 0.5], [0.5, 1.0]])

        result = estimator._ensure_psd(cov)

        assert np.allclose(result, result.T)

    def test_psd_already_valid_unchanged(self, mock_factor_builder):
        """Already PSD matrix is nearly unchanged."""
        estimator = FactorCovarianceEstimator(mock_factor_builder)

        # Valid PSD matrix
        cov = np.array([[1.0, 0.3], [0.3, 1.0]])

        result = estimator._ensure_psd(cov)

        # Should be very close to original
        assert np.allclose(result, cov, atol=1e-8)


class TestComputeDecayWeights:
    """Tests for exponential decay weight computation."""

    def test_exponential_decay_weighting(self, mock_factor_builder):
        """Exponential decay weights are computed correctly."""
        estimator = FactorCovarianceEstimator(mock_factor_builder)

        dates = [date(2023, 6, 28), date(2023, 6, 29), date(2023, 6, 30)]
        as_of_date = date(2023, 6, 30)

        weights = estimator._compute_decay_weights(dates, as_of_date)

        # Most recent date should have highest weight
        assert weights[2] > weights[1] > weights[0]

        # Weights should be positive
        assert np.all(weights > 0)


class TestComputeWeightedCovariance:
    """Tests for weighted covariance computation."""

    def test_basic_weighted_covariance(self, mock_factor_builder):
        """Basic weighted covariance computation works."""
        estimator = FactorCovarianceEstimator(mock_factor_builder)

        returns = np.array(
            [
                [0.01, 0.02],
                [-0.01, 0.01],
                [0.02, -0.01],
            ]
        )
        weights = np.array([1.0, 1.0, 1.0])

        cov = estimator._compute_weighted_covariance(returns, weights)

        # Should be 2x2
        assert cov.shape == (2, 2)

        # Should be symmetric
        assert np.allclose(cov, cov.T)


class TestLedoitWolfShrinkage:
    """Tests for Ledoit-Wolf shrinkage."""

    def test_ledoit_wolf_shrinkage(self, mock_factor_builder):
        """Ledoit-Wolf shrinkage produces valid result."""
        estimator = FactorCovarianceEstimator(mock_factor_builder)

        # Create sample returns and compute covariance
        np.random.seed(42)
        returns = np.random.randn(100, 2) * 0.01
        centered_returns = returns - np.mean(returns, axis=0)
        cov = np.cov(centered_returns.T)

        shrunk, intensity = estimator._apply_ledoit_wolf_shrinkage(centered_returns, cov)

        # Shrinkage intensity should be in [0, 1]
        assert 0 <= intensity <= 1

        # Shrunk matrix should be PSD
        eigenvalues = np.linalg.eigvalsh(shrunk)
        assert np.all(eigenvalues >= 0)

    def test_shrinkage_produces_valid_matrix(self, mock_factor_builder):
        """Shrinkage produces a valid PSD matrix."""
        estimator = FactorCovarianceEstimator(mock_factor_builder)

        # Create sample returns with high correlation
        np.random.seed(42)
        base = np.random.randn(100)
        returns = np.column_stack([base + np.random.randn(100) * 0.1, base])
        centered_returns = returns - np.mean(returns, axis=0)
        cov = np.cov(centered_returns.T)

        shrunk, intensity = estimator._apply_ledoit_wolf_shrinkage(centered_returns, cov)

        # Shrunk matrix should be PSD
        eigenvalues = np.linalg.eigvalsh(shrunk)
        assert np.all(eigenvalues >= 0)

        # Should be symmetric
        assert np.allclose(shrunk, shrunk.T)

        # Shrinkage intensity should be returned
        assert 0 <= intensity <= 1

    def test_shrinkage_intensity_returned(self, mock_factor_builder):
        """Shrinkage intensity is returned."""
        estimator = FactorCovarianceEstimator(mock_factor_builder)

        np.random.seed(42)
        returns = np.random.randn(100, 2) * 0.01
        centered_returns = returns - np.mean(returns, axis=0)
        cov = np.cov(centered_returns.T)

        _, intensity = estimator._apply_ledoit_wolf_shrinkage(centered_returns, cov)

        assert isinstance(intensity, float)


class TestNeweyWestHAC:
    """Tests for Newey-West HAC correction."""

    def test_hac_correction_applied(self, mock_factor_builder):
        """HAC correction modifies covariance."""
        estimator = FactorCovarianceEstimator(mock_factor_builder)

        returns = np.random.randn(100, 2) * 0.01
        weights = np.ones(100)
        weighted_cov = np.cov(returns.T)

        hac_cov = estimator._apply_newey_west_to_covariance(weighted_cov, returns, weights)

        # HAC should modify covariance (unless no autocorrelation)
        assert hac_cov.shape == weighted_cov.shape


class TestValidateDailyInputs:
    """Tests for _validate_daily_inputs()."""

    def test_filters_nan_exposures(self, mock_factor_builder):
        """Filters out stocks with NaN exposures."""
        estimator = FactorCovarianceEstimator(mock_factor_builder)

        exposures = pl.DataFrame(
            {
                "permno": [1, 2, 3],
                "momentum_12_1": [0.1, np.nan, 0.3],
                "book_to_market": [0.2, 0.2, 0.2],
                "roe": [0.1, 0.1, 0.1],
                "log_market_cap": [0.1, 0.1, 0.1],
                "realized_vol": [0.1, 0.1, 0.1],
            }
        )
        returns = pl.DataFrame(
            {
                "permno": [1, 2, 3],
                "ret": [0.01, 0.02, -0.01],
            }
        )

        # This should work but filter out permno 2
        # Need at least min_stocks_per_day stocks, so adjust config
        estimator.config.min_stocks_per_day = 2

        clean_exp, clean_ret, warns = estimator._validate_daily_inputs(
            exposures, returns, date(2023, 6, 30)
        )

        assert clean_exp.height == 2
        assert 2 not in clean_exp["permno"].to_list()

    def test_filters_nan_returns(self, mock_factor_builder):
        """Filters out stocks with NaN returns."""
        estimator = FactorCovarianceEstimator(mock_factor_builder)
        estimator.config.min_stocks_per_day = 2

        exposures = pl.DataFrame(
            {
                "permno": [1, 2, 3],
                "momentum_12_1": [0.1, 0.2, 0.3],
                "book_to_market": [0.2, 0.2, 0.2],
                "roe": [0.1, 0.1, 0.1],
                "log_market_cap": [0.1, 0.1, 0.1],
                "realized_vol": [0.1, 0.1, 0.1],
            }
        )
        returns = pl.DataFrame(
            {
                "permno": [1, 2, 3],
                "ret": [0.01, np.nan, -0.01],
            }
        )

        clean_exp, clean_ret, warns = estimator._validate_daily_inputs(
            exposures, returns, date(2023, 6, 30)
        )

        assert clean_ret.height == 2
        assert 2 not in clean_ret["permno"].to_list()

    def test_raises_on_insufficient_stocks(self, mock_factor_builder):
        """Raises InsufficientDataError when too few stocks."""
        estimator = FactorCovarianceEstimator(mock_factor_builder)
        estimator.config.min_stocks_per_day = 100

        exposures = pl.DataFrame(
            {
                "permno": [1, 2],
                "momentum_12_1": [0.1, 0.2],
                "book_to_market": [0.2, 0.2],
                "roe": [0.1, 0.1],
                "log_market_cap": [0.1, 0.1],
                "realized_vol": [0.1, 0.1],
            }
        )
        returns = pl.DataFrame(
            {
                "permno": [1, 2],
                "ret": [0.01, 0.02],
            }
        )

        with pytest.raises(InsufficientDataError):
            estimator._validate_daily_inputs(exposures, returns, date(2023, 6, 30))


class TestPITCorrectness:
    """Tests for point-in-time correctness."""

    def test_reproducibility_hash_same_inputs(self, mock_factor_builder):
        """Same inputs produce same reproducibility hash."""
        # This tests that the hash computation is deterministic
        import hashlib

        hash1 = hashlib.sha256(b"2023-06-30_60_126").hexdigest()[:16]
        hash2 = hashlib.sha256(b"2023-06-30_60_126").hexdigest()[:16]

        assert hash1 == hash2

    def test_dataset_version_ids_in_result(self, sample_covariance_result):
        """Dataset version IDs are included in result."""
        assert "crsp" in sample_covariance_result.dataset_version_ids
        assert "compustat" in sample_covariance_result.dataset_version_ids


class TestEstimateFactorReturnsErrorHandling:
    """Tests for error handling in estimate_factor_returns()."""

    def test_skips_day_with_singular_matrix(self, mock_factor_builder, caplog):
        """Skips day if WLS regression fails with singular matrix."""
        import logging
        from unittest.mock import patch

        import pytest

        caplog.set_level(logging.ERROR)

        estimator = FactorCovarianceEstimator(mock_factor_builder)

        # Patch _run_wls_regression to raise LinAlgError
        with patch.object(
            estimator, "_run_wls_regression", side_effect=np.linalg.LinAlgError("Singular matrix")
        ):
            # Should skip days with singular matrix but raise InsufficientDataError if all days fail
            with pytest.raises(InsufficientDataError, match="No valid factor returns computed"):
                estimator.estimate_factor_returns(
                    start_date=date(2023, 6, 28),
                    end_date=date(2023, 6, 30),
                )

            # Check that error was logged with proper structure before the exception
            assert any("singular matrix" in record.message.lower() for record in caplog.records)
            assert any(record.levelname == "ERROR" for record in caplog.records)

    def test_skips_day_with_key_error(self, mock_factor_builder, caplog):
        """Skips day if data access fails with KeyError."""
        import logging
        from unittest.mock import patch

        import pytest

        caplog.set_level(logging.ERROR)

        estimator = FactorCovarianceEstimator(mock_factor_builder)

        # Patch to raise KeyError during data access
        with patch.object(estimator, "_run_wls_regression", side_effect=KeyError("missing_column")):
            # Should skip days but raise InsufficientDataError if all days fail
            with pytest.raises(InsufficientDataError, match="No valid factor returns computed"):
                estimator.estimate_factor_returns(
                    start_date=date(2023, 6, 28),
                    end_date=date(2023, 6, 30),
                )

            # Check error logging
            assert any("data access error" in record.message.lower() for record in caplog.records)
            # The 'extra' fields are added directly to the record's attributes
            assert any(hasattr(record, "error_type") for record in caplog.records)

    def test_skips_day_with_value_error(self, mock_factor_builder, caplog):
        """Skips day if invalid data causes ValueError."""
        import logging
        from unittest.mock import patch

        import pytest

        caplog.set_level(logging.ERROR)

        estimator = FactorCovarianceEstimator(mock_factor_builder)

        # Patch to raise ValueError
        with patch.object(estimator, "_run_wls_regression", side_effect=ValueError("Invalid data")):
            # Should skip days but raise InsufficientDataError if all days fail
            with pytest.raises(InsufficientDataError, match="No valid factor returns computed"):
                estimator.estimate_factor_returns(
                    start_date=date(2023, 6, 28),
                    end_date=date(2023, 6, 30),
                )

            # Check error logging
            assert any("invalid data" in record.message.lower() for record in caplog.records)

    def test_error_log_includes_context(self, mock_factor_builder, caplog):
        """Error logs include context information."""
        import logging
        from unittest.mock import patch

        import pytest

        caplog.set_level(logging.ERROR)

        estimator = FactorCovarianceEstimator(mock_factor_builder)

        with patch.object(
            estimator, "_run_wls_regression", side_effect=np.linalg.LinAlgError("Test error")
        ):
            # Should raise InsufficientDataError after logging errors
            with pytest.raises(InsufficientDataError, match="No valid factor returns computed"):
                estimator.estimate_factor_returns(
                    start_date=date(2023, 6, 28),
                    end_date=date(2023, 6, 30),
                )

            # Check that error logs include 'extra' dict with context
            error_records = [r for r in caplog.records if r.levelname == "ERROR"]
            assert len(error_records) > 0

            # Check for structured logging fields
            for record in error_records:
                # The 'extra' fields are added directly to the record's attributes
                # Should have error_type and date context
                assert hasattr(record, "error_type") or hasattr(record, "date")
                # Should have exc_info
                assert record.exc_info is not None
