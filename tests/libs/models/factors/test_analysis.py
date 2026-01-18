"""Tests for FactorAnalysisService normal behavior."""

from __future__ import annotations

from datetime import date
from unittest.mock import Mock

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from libs.models.factors.analysis import FactorAnalysisService


@pytest.fixture()
def mock_builder() -> Mock:
    """Create a mock FactorBuilder."""
    return Mock()


@pytest.fixture()
def analysis_service(mock_builder: Mock) -> FactorAnalysisService:
    """Create FactorAnalysisService with mock builder."""
    return FactorAnalysisService(mock_builder)


@pytest.fixture()
def portfolio_weights() -> pl.DataFrame:
    """Sample portfolio weights for testing."""
    return pl.DataFrame(
        {
            "permno": [1, 2],
            "weight": [0.6, 0.4],
        }
    )


class TestComputePortfolioExposure:
    """Tests for compute_portfolio_exposure success paths."""

    def test_aggregates_exposures_and_coverage(
        self,
        analysis_service: FactorAnalysisService,
        mock_builder: Mock,
        portfolio_weights: pl.DataFrame,
    ) -> None:
        """Aggregate exposure uses contributions and coverage uses weights."""

        momentum = Mock()
        momentum.exposures = pl.DataFrame(
            {
                "permno": [1, 2],
                "zscore": [1.0, -0.5],
            }
        )

        value = Mock()
        value.exposures = pl.DataFrame(
            {
                "permno": [1],
                "zscore": [2.0],
            }
        )

        def side_effect(factor_name: str, as_of_date: date, universe: list[int]) -> Mock:
            if factor_name == "momentum":
                return momentum
            if factor_name == "value":
                return value
            raise ValueError("unexpected factor")

        mock_builder.compute_factor.side_effect = side_effect

        result = analysis_service.compute_portfolio_exposure(
            portfolio_weights=portfolio_weights,
            factor_names=["momentum", "value"],
            as_of_date=date(2024, 2, 1),
        )

        expected_exposures = pl.DataFrame(
            {
                "factor": ["momentum", "value"],
                "exposure": [1.0 * 0.6 + (-0.5 * 0.4), 2.0 * 0.6],
            }
        ).sort("factor")

        expected_coverage = pl.DataFrame(
            {
                "factor": ["momentum", "value"],
                "coverage_pct": [0.6 + 0.4, 0.6],
            }
        ).sort("factor")

        assert_frame_equal(result.exposures.sort("factor"), expected_exposures)
        assert_frame_equal(result.coverage.sort("factor"), expected_coverage)
        assert result.stock_exposures.height == 3
        assert set(result.stock_exposures["factor"].to_list()) == {"momentum", "value"}

    def test_empty_portfolio_returns_empty_result(
        self, analysis_service: FactorAnalysisService
    ) -> None:
        """Empty portfolios should short-circuit to empty results."""
        empty_portfolio = pl.DataFrame(schema={"permno": pl.Int64, "weight": pl.Float64})

        result = analysis_service.compute_portfolio_exposure(
            portfolio_weights=empty_portfolio,
            factor_names=["momentum"],
            as_of_date=date(2024, 2, 1),
        )

        assert result.exposures.is_empty()
        assert result.stock_exposures.is_empty()
        assert result.coverage.is_empty()

    def test_builder_called_with_unique_universe(
        self,
        analysis_service: FactorAnalysisService,
        mock_builder: Mock,
    ) -> None:
        """Universe should be de-duplicated before factor computation."""
        portfolio = pl.DataFrame(
            {
                "permno": [10, 10, 11],
                "weight": [0.5, 0.2, 0.3],
            }
        )
        mock_result = Mock()
        mock_result.exposures = pl.DataFrame(
            {
                "permno": [10, 11],
                "zscore": [0.1, 0.2],
            }
        )
        mock_builder.compute_factor.return_value = mock_result

        analysis_service.compute_portfolio_exposure(
            portfolio_weights=portfolio,
            factor_names=["momentum"],
            as_of_date=date(2024, 2, 1),
        )

        call_kwargs = mock_builder.compute_factor.call_args.kwargs
        assert set(call_kwargs["universe"]) == {10, 11}
