"""Tests for exception handling in FactorAnalysisService.

These tests verify that the service properly handles various error conditions
during factor computation with specific exception types and appropriate logging.
"""

from datetime import date
from unittest.mock import Mock

import polars as pl
import pytest

from libs.models.factors.analysis import FactorAnalysisService


@pytest.fixture()
def mock_builder():
    """Create a mock FactorBuilder."""
    return Mock()


@pytest.fixture()
def analysis_service(mock_builder):
    """Create FactorAnalysisService with mock builder."""
    return FactorAnalysisService(mock_builder)


@pytest.fixture()
def sample_portfolio_weights() -> pl.DataFrame:
    """Sample portfolio weights for testing."""
    return pl.DataFrame(
        {
            "permno": [100, 101, 102],
            "weight": [0.4, 0.35, 0.25],
        }
    )


class TestComputePortfolioExposureExceptionHandling:
    """Tests for exception handling in compute_portfolio_exposure."""

    def test_handles_key_error_in_factor_computation(
        self,
        analysis_service: FactorAnalysisService,
        mock_builder: Mock,
        sample_portfolio_weights: pl.DataFrame,
    ) -> None:
        """Test that KeyError during factor computation is handled gracefully."""
        # Mock builder to raise KeyError
        mock_builder.compute_factor.side_effect = KeyError("Missing data column")

        result = analysis_service.compute_portfolio_exposure(
            portfolio_weights=sample_portfolio_weights,
            factor_names=["momentum_12m", "log_market_cap"],
            as_of_date=date(2024, 1, 15),
        )

        # Should return empty result instead of crashing
        assert result.exposures.is_empty()
        assert result.stock_exposures.is_empty()
        assert result.coverage.is_empty()

    def test_handles_value_error_in_factor_computation(
        self,
        analysis_service: FactorAnalysisService,
        mock_builder: Mock,
        sample_portfolio_weights: pl.DataFrame,
    ) -> None:
        """Test that ValueError during factor computation is handled gracefully."""
        # Mock builder to raise ValueError
        mock_builder.compute_factor.side_effect = ValueError("Invalid parameter")

        result = analysis_service.compute_portfolio_exposure(
            portfolio_weights=sample_portfolio_weights,
            factor_names=["momentum_12m"],
            as_of_date=date(2024, 1, 15),
        )

        # Should return empty result instead of crashing
        assert result.exposures.is_empty()
        assert result.stock_exposures.is_empty()
        assert result.coverage.is_empty()

    def test_handles_polars_compute_error(
        self,
        analysis_service: FactorAnalysisService,
        mock_builder: Mock,
        sample_portfolio_weights: pl.DataFrame,
    ) -> None:
        """Test that Polars ComputeError is handled gracefully."""
        # Mock builder to raise ComputeError
        mock_builder.compute_factor.side_effect = pl.ComputeError("Computation failed")

        result = analysis_service.compute_portfolio_exposure(
            portfolio_weights=sample_portfolio_weights,
            factor_names=["momentum_12m"],
            as_of_date=date(2024, 1, 15),
        )

        # Should return empty result instead of crashing
        assert result.exposures.is_empty()
        assert result.stock_exposures.is_empty()
        assert result.coverage.is_empty()

    def test_handles_polars_column_not_found_error(
        self,
        analysis_service: FactorAnalysisService,
        mock_builder: Mock,
        sample_portfolio_weights: pl.DataFrame,
    ) -> None:
        """Test that Polars ColumnNotFoundError is handled gracefully."""
        # Mock builder to raise ColumnNotFoundError
        mock_builder.compute_factor.side_effect = pl.ColumnNotFoundError("Column not found")

        result = analysis_service.compute_portfolio_exposure(
            portfolio_weights=sample_portfolio_weights,
            factor_names=["momentum_12m"],
            as_of_date=date(2024, 1, 15),
        )

        # Should return empty result instead of crashing
        assert result.exposures.is_empty()
        assert result.stock_exposures.is_empty()
        assert result.coverage.is_empty()

    def test_partial_success_with_mixed_errors(
        self,
        analysis_service: FactorAnalysisService,
        mock_builder: Mock,
        sample_portfolio_weights: pl.DataFrame,
    ) -> None:
        """Test that partial success is achieved when some factors fail."""

        # Create a successful mock result
        successful_result = Mock()
        successful_result.exposures = pl.DataFrame(
            {
                "permno": [100, 101, 102],
                "zscore": [1.5, -0.5, 0.2],
            }
        )

        # Mock builder to succeed for first factor, fail for second
        def compute_factor_side_effect(factor_name, as_of_date, universe):
            if factor_name == "momentum_12m":
                return successful_result
            elif factor_name == "log_market_cap":
                raise KeyError("Missing column")

        mock_builder.compute_factor.side_effect = compute_factor_side_effect

        result = analysis_service.compute_portfolio_exposure(
            portfolio_weights=sample_portfolio_weights,
            factor_names=["momentum_12m", "log_market_cap"],
            as_of_date=date(2024, 1, 15),
        )

        # Should have results for the successful factor only
        assert not result.exposures.is_empty()
        assert not result.stock_exposures.is_empty()
        # Should only have data for momentum_12m
        assert result.exposures.height == 1
        assert result.exposures["factor"][0] == "momentum_12m"

    def test_missing_zscore_column_handled_gracefully(
        self,
        analysis_service: FactorAnalysisService,
        mock_builder: Mock,
        sample_portfolio_weights: pl.DataFrame,
    ) -> None:
        """Test that missing zscore column is handled with warning (not error)."""

        # Create a mock result without zscore column
        result_without_zscore = Mock()
        result_without_zscore.exposures = pl.DataFrame(
            {
                "permno": [100, 101, 102],
                "value": [1.5, -0.5, 0.2],  # No zscore column
            }
        )

        mock_builder.compute_factor.return_value = result_without_zscore

        result = analysis_service.compute_portfolio_exposure(
            portfolio_weights=sample_portfolio_weights,
            factor_names=["momentum_12m"],
            as_of_date=date(2024, 1, 15),
        )

        # Should return empty result (not crash)
        assert result.exposures.is_empty()
        assert result.stock_exposures.is_empty()
        assert result.coverage.is_empty()
