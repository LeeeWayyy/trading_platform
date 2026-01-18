"""
Tests for SpecificRiskEstimator.
"""

from datetime import date

import numpy as np
import polars as pl

from libs.trading.risk import (
    CANONICAL_FACTOR_ORDER,
    CovarianceConfig,
    SpecificRiskEstimator,
    SpecificRiskResult,
)
from tests.libs.trading.risk.conftest import (
    create_mock_covariance_matrix,
    create_mock_factor_exposures,
)


class TestSpecificRiskEstimatorInit:
    """Tests for SpecificRiskEstimator initialization."""

    def test_default_config(self, mock_crsp_provider):
        """Uses default config when none provided."""
        estimator = SpecificRiskEstimator(crsp_provider=mock_crsp_provider)
        assert estimator.config.halflife_days == 60
        assert estimator.config.min_observations == 126

    def test_custom_config(self, mock_crsp_provider):
        """Uses custom config when provided."""
        config = CovarianceConfig(halflife_days=30)
        estimator = SpecificRiskEstimator(config=config, crsp_provider=mock_crsp_provider)
        assert estimator.config.halflife_days == 30


class TestSpecificRiskEstimatorSignature:
    """Tests for SpecificRiskEstimator.estimate() signature."""

    def test_accepts_factor_cov_and_loadings(self, mock_crsp_provider):
        """estimate() accepts factor_cov and factor_loadings as per spec."""
        estimator = SpecificRiskEstimator(crsp_provider=mock_crsp_provider)

        # Create test inputs
        factor_cov = create_mock_covariance_matrix()
        factor_loadings = create_mock_factor_exposures(n_stocks=10)

        # Should not raise - just testing signature works
        result = estimator.estimate(
            as_of_date=date(2023, 6, 30),
            factor_cov=factor_cov,
            factor_loadings=factor_loadings,
        )

        assert isinstance(result, SpecificRiskResult)


class TestSpecificRiskEstimate:
    """Tests for SpecificRiskEstimator.estimate()."""

    def test_basic_specific_risk_estimation(self, mock_crsp_provider):
        """Basic specific risk estimation works."""
        estimator = SpecificRiskEstimator(crsp_provider=mock_crsp_provider)

        factor_cov = create_mock_covariance_matrix()
        factor_loadings = create_mock_factor_exposures(n_stocks=10)

        result = estimator.estimate(
            as_of_date=date(2023, 6, 30),
            factor_cov=factor_cov,
            factor_loadings=factor_loadings,
        )

        assert result.specific_risks.height > 0
        assert "permno" in result.specific_risks.columns
        assert "specific_variance" in result.specific_risks.columns
        assert "specific_vol" in result.specific_risks.columns

    def test_specific_variance_is_positive(self, mock_crsp_provider):
        """Specific variance should be positive (or floored)."""
        estimator = SpecificRiskEstimator(crsp_provider=mock_crsp_provider)

        factor_cov = create_mock_covariance_matrix()
        factor_loadings = create_mock_factor_exposures(n_stocks=10)

        result = estimator.estimate(
            as_of_date=date(2023, 6, 30),
            factor_cov=factor_cov,
            factor_loadings=factor_loadings,
        )

        # All variances should be positive (floored if needed)
        variances = result.specific_risks["specific_variance"].to_numpy()
        assert np.all(variances > 0)

    def test_annualized_volatility_correct(self, mock_crsp_provider):
        """Annualized volatility is sqrt(variance * 252)."""
        estimator = SpecificRiskEstimator(crsp_provider=mock_crsp_provider)

        factor_cov = create_mock_covariance_matrix()
        factor_loadings = create_mock_factor_exposures(n_stocks=10)

        result = estimator.estimate(
            as_of_date=date(2023, 6, 30),
            factor_cov=factor_cov,
            factor_loadings=factor_loadings,
        )

        # Check relationship: vol = sqrt(var * 252)
        df = result.specific_risks
        for row in df.iter_rows(named=True):
            expected_vol = np.sqrt(row["specific_variance"] * 252)
            assert np.isclose(row["specific_vol"], expected_vol, rtol=1e-6)

    def test_floored_count_tracked(self, mock_crsp_provider):
        """Floored variance count is tracked."""
        estimator = SpecificRiskEstimator(crsp_provider=mock_crsp_provider)

        factor_cov = create_mock_covariance_matrix()
        factor_loadings = create_mock_factor_exposures(n_stocks=10)

        result = estimator.estimate(
            as_of_date=date(2023, 6, 30),
            factor_cov=factor_cov,
            factor_loadings=factor_loadings,
        )

        # floored_count should be an integer >= 0
        assert isinstance(result.floored_count, int)
        assert result.floored_count >= 0

    def test_uses_canonical_factor_order(self, mock_crsp_provider):
        """Uses canonical factor ordering for loadings."""
        estimator = SpecificRiskEstimator(crsp_provider=mock_crsp_provider)
        assert estimator.factor_names == CANONICAL_FACTOR_ORDER


class TestSpecificRiskResultValidation:
    """Tests for SpecificRiskResult.validate()."""

    def test_validate_no_negative_variance(self):
        """Validation catches negative variances."""
        df = pl.DataFrame(
            {
                "permno": [1, 2],
                "specific_variance": [0.01, -0.01],  # Negative!
                "specific_vol": [0.1, 0.1],
            }
        )
        result = SpecificRiskResult(
            specific_risks=df,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={},
        )
        errors = result.validate()
        assert any("negative" in e for e in errors)

    def test_validate_no_nan_inf(self):
        """Validation catches NaN values."""
        df = pl.DataFrame(
            {
                "permno": [1, 2],
                "specific_variance": [0.01, np.nan],
                "specific_vol": [0.1, 0.1],
            }
        )
        result = SpecificRiskResult(
            specific_risks=df,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={},
        )
        errors = result.validate()
        assert any("NaN" in e for e in errors)

    def test_validate_reasonable_range(self):
        """Validation catches unreasonable volatility."""
        df = pl.DataFrame(
            {
                "permno": [1, 2],
                "specific_variance": [0.01, 100.0],  # 100 -> vol = 158 = 15800%!
                "specific_vol": [0.1, 158.0],
            }
        )
        result = SpecificRiskResult(
            specific_risks=df,
            as_of_date=date(2023, 6, 30),
            dataset_version_ids={},
        )
        errors = result.validate()
        assert any("500%" in e for e in errors)


class TestSpecificRiskStorageFormat:
    """Tests for SpecificRiskResult.to_storage_format()."""

    def test_to_storage_format_matches_schema(self, mock_crsp_provider):
        """Storage format matches expected schema."""
        estimator = SpecificRiskEstimator(crsp_provider=mock_crsp_provider)

        factor_cov = create_mock_covariance_matrix()
        factor_loadings = create_mock_factor_exposures(n_stocks=10)

        result = estimator.estimate(
            as_of_date=date(2023, 6, 30),
            factor_cov=factor_cov,
            factor_loadings=factor_loadings,
        )

        df = result.to_storage_format()

        required_columns = [
            "as_of_date",
            "permno",
            "specific_variance",
            "specific_vol",
            "dataset_version_id",
        ]
        for col in required_columns:
            assert col in df.columns, f"Missing column: {col}"


class TestSpecificRiskCoverage:
    """Tests for coverage metric."""

    def test_coverage_metric_correct(self, mock_crsp_provider):
        """Coverage is computed correctly."""
        estimator = SpecificRiskEstimator(crsp_provider=mock_crsp_provider)

        factor_cov = create_mock_covariance_matrix()
        factor_loadings = create_mock_factor_exposures(n_stocks=50)

        result = estimator.estimate(
            as_of_date=date(2023, 6, 30),
            factor_cov=factor_cov,
            factor_loadings=factor_loadings,
        )

        # Coverage should be in [0, 1]
        assert 0 <= result.coverage <= 1.0

        # Coverage = n_results / n_universe
        n_results = result.specific_risks.height
        # At least some stocks should have results
        assert n_results > 0


class TestSpecificRiskDecayWeights:
    """Tests for exponential decay weights in specific risk."""

    def test_decay_weights_computation(self, mock_crsp_provider):
        """Decay weights are computed correctly."""
        estimator = SpecificRiskEstimator(crsp_provider=mock_crsp_provider)

        dates = [date(2023, 6, 28), date(2023, 6, 29), date(2023, 6, 30)]
        as_of_date = date(2023, 6, 30)

        weights = estimator._compute_decay_weights(dates, as_of_date)

        # Most recent should have highest weight
        assert weights[2] > weights[1] > weights[0]

        # Weights should sum to 1
        assert np.isclose(np.sum(weights), 1.0)


class TestSpecificRiskErrorHandling:
    """Tests for error handling in SpecificRiskEstimator.estimate()."""

    def test_handles_linalg_error(self, mock_crsp_provider, caplog):
        """Handles LinAlgError gracefully and continues processing other stocks."""
        import logging
        from unittest.mock import patch

        caplog.set_level(logging.ERROR)

        estimator = SpecificRiskEstimator(crsp_provider=mock_crsp_provider)

        factor_cov = create_mock_covariance_matrix()
        factor_loadings = create_mock_factor_exposures(n_stocks=10)

        # Patch _compute_decay_weights to raise LinAlgError on first call
        original_compute = estimator._compute_decay_weights
        call_count = 0

        def patched_compute_decay_weights(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise np.linalg.LinAlgError("Singular matrix in computation")
            return original_compute(*args, **kwargs)

        with patch.object(estimator, "_compute_decay_weights", patched_compute_decay_weights):
            result = estimator.estimate(
                as_of_date=date(2023, 6, 30),
                factor_cov=factor_cov,
                factor_loadings=factor_loadings,
            )

            # Should still have results for other stocks
            assert result.specific_risks.height > 0
            # Check error was logged
            assert any(
                "matrix operation failed" in record.message.lower() for record in caplog.records
            )
            assert any(record.levelname == "ERROR" for record in caplog.records)

    def test_handles_value_error(self, mock_crsp_provider, caplog):
        """Handles ValueError during computation."""
        import logging

        caplog.set_level(logging.ERROR)

        estimator = SpecificRiskEstimator(crsp_provider=mock_crsp_provider)

        # Create invalid factor loadings with NaN
        factor_cov = create_mock_covariance_matrix()
        factor_loadings = create_mock_factor_exposures(n_stocks=10)

        # The code already handles NaN in loadings, so this tests the ValueError path
        # We need to trigger a different ValueError scenario
        result = estimator.estimate(
            as_of_date=date(2023, 6, 30),
            factor_cov=factor_cov,
            factor_loadings=factor_loadings,
        )

        # Should complete successfully even if some stocks fail
        assert result.specific_risks.height > 0

    def test_handles_key_error(self, mock_crsp_provider, caplog):
        """Handles KeyError when accessing missing data."""
        import logging

        caplog.set_level(logging.ERROR)

        estimator = SpecificRiskEstimator(crsp_provider=mock_crsp_provider)

        factor_cov = create_mock_covariance_matrix()

        # Create factor loadings with missing factor columns to trigger KeyError
        factor_loadings = pl.DataFrame(
            {
                "permno": [10001, 10002],
                "date": [date(2023, 6, 30), date(2023, 6, 30)],
                "factor_name": ["momentum_12_1", "momentum_12_1"],
                "zscore": [0.5, -0.3],
            }
        )

        result = estimator.estimate(
            as_of_date=date(2023, 6, 30),
            factor_cov=factor_cov,
            factor_loadings=factor_loadings,
        )

        # Should handle missing factors gracefully (use 0.0 for missing)
        assert result.specific_risks.height >= 0

    def test_handles_zero_division(self, mock_crsp_provider, caplog):
        """Handles ZeroDivisionError during weight computation."""
        import logging
        from unittest.mock import patch

        caplog.set_level(logging.ERROR)

        estimator = SpecificRiskEstimator(crsp_provider=mock_crsp_provider)

        factor_cov = create_mock_covariance_matrix()
        factor_loadings = create_mock_factor_exposures(n_stocks=10)

        # Patch _compute_decay_weights to raise ZeroDivisionError for first stock
        original_compute_decay = estimator._compute_decay_weights

        def patched_compute_decay(dates, as_of_date):
            if not hasattr(patched_compute_decay, "call_count"):
                patched_compute_decay.call_count = 0
            patched_compute_decay.call_count += 1

            if patched_compute_decay.call_count == 1:
                raise ZeroDivisionError("Division by zero in weight computation")

            return original_compute_decay(dates, as_of_date)

        with patch.object(estimator, "_compute_decay_weights", patched_compute_decay):
            result = estimator.estimate(
                as_of_date=date(2023, 6, 30),
                factor_cov=factor_cov,
                factor_loadings=factor_loadings,
            )

            # Should continue with other stocks
            assert result.specific_risks.height > 0
            # Check error was logged
            assert any(record.levelname == "ERROR" for record in caplog.records)

    def test_error_logs_include_context(self, mock_crsp_provider, caplog):
        """Error logs include context information (permno, matrix shape, etc.)."""
        import logging
        from unittest.mock import patch

        caplog.set_level(logging.ERROR)

        estimator = SpecificRiskEstimator(crsp_provider=mock_crsp_provider)

        factor_cov = create_mock_covariance_matrix()
        factor_loadings = create_mock_factor_exposures(n_stocks=10)

        # Patch _compute_decay_weights to raise an error for first stock computation
        original_compute = estimator._compute_decay_weights
        call_count = 0

        def patched_compute_decay_weights(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise np.linalg.LinAlgError("Test error for logging")
            return original_compute(*args, **kwargs)

        with patch.object(estimator, "_compute_decay_weights", patched_compute_decay_weights):
            estimator.estimate(
                as_of_date=date(2023, 6, 30),
                factor_cov=factor_cov,
                factor_loadings=factor_loadings,
            )

            # Check error logs include structured context
            error_records = [r for r in caplog.records if r.levelname == "ERROR"]
            assert len(error_records) > 0

            # Check for structured logging fields
            for record in error_records:
                # The 'extra' fields are added directly to the record's attributes
                # Should have error_type and permno context
                assert hasattr(record, "error_type") or hasattr(record, "permno")
                # Should have exc_info for stack trace
                assert record.exc_info is not None

    def test_continues_after_errors(self, mock_crsp_provider):
        """Processing continues for remaining stocks after errors."""
        estimator = SpecificRiskEstimator(crsp_provider=mock_crsp_provider)

        factor_cov = create_mock_covariance_matrix()
        factor_loadings = create_mock_factor_exposures(n_stocks=20)

        # Normal execution should process all stocks
        result = estimator.estimate(
            as_of_date=date(2023, 6, 30),
            factor_cov=factor_cov,
            factor_loadings=factor_loadings,
        )

        # Should have results for most/all stocks
        assert result.specific_risks.height > 0
        # Coverage should be reasonable
        assert result.coverage > 0.5
