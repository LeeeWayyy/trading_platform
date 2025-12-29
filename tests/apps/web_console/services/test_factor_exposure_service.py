"""Tests for FactorExposureService."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from apps.web_console.services.factor_exposure_service import FactorExposureService
from libs.factors.factor_definitions import FactorResult


def _mock_factor_result() -> FactorResult:
    """Build a FactorResult with predictable exposures.

    Returns:
        FactorResult with exposures for two factors and two permnos.

    Example:
        >>> result = _mock_factor_result()
        >>> set(result.exposures["factor_name"].unique())
        {'momentum_12_1', 'book_to_market'}
    """

    exposures = pl.DataFrame(
        {
            "permno": [1, 2, 1, 2],
            "factor_name": [
                "momentum_12_1",
                "momentum_12_1",
                "book_to_market",
                "book_to_market",
            ],
            "zscore": [1.0, 2.0, 0.5, -0.5],
            "raw_value": [0.1, 0.2, 0.3, -0.3],
            "percentile": [0.6, 0.8, 0.7, 0.4],
            "date": [date(2024, 1, 5)] * 4,
        }
    )

    return FactorResult(
        exposures=exposures,
        as_of_date=date(2024, 1, 5),
        dataset_version_ids={},
    )


@pytest.fixture()
def mock_factor_builder() -> MagicMock:
    """Return a FactorBuilder stub with compute_all_factors mocked."""

    builder = MagicMock()
    builder.compute_all_factors.return_value = _mock_factor_result()
    return builder


@pytest.fixture()
def mock_service(mock_factor_builder: MagicMock) -> FactorExposureService:
    """Create FactorExposureService with mocked dependencies."""

    return FactorExposureService(
        factor_builder=mock_factor_builder,
        db_adapter=None,
        redis_client=None,
        user={"role": "admin", "user_id": "test_user"},
    )


def test_get_factor_definitions_returns_entries(mock_service: FactorExposureService) -> None:
    """Ensure canonical factors are returned with metadata."""

    factor_defs = mock_service.get_factor_definitions()

    assert factor_defs
    assert all(fd.name for fd in factor_defs)
    assert all(fd.category for fd in factor_defs)
    assert all(fd.description for fd in factor_defs)


def test_get_portfolio_exposures_weighted(mock_service: FactorExposureService) -> None:
    """Verify portfolio exposures are weight-averaged by holdings."""

    holdings = pl.DataFrame(
        {
            "permno": [1, 2],
            "symbol": ["AAA", "BBB"],
            "weight": [0.6, 0.4],
        }
    )
    mock_service._get_portfolio_holdings = MagicMock(return_value=holdings)

    result = mock_service.get_portfolio_exposures(
        portfolio_id="global",
        start_date=date(2024, 1, 5),
        end_date=date(2024, 1, 5),
        factors=["momentum_12_1", "book_to_market"],
    )

    assert result.exposures.height == 2

    momentum = (
        result.exposures.filter(pl.col("factor") == "momentum_12_1")
        .select("exposure")
        .item()
    )
    book_to_market = (
        result.exposures.filter(pl.col("factor") == "book_to_market")
        .select("exposure")
        .item()
    )

    assert pytest.approx(momentum, rel=1e-6) == 1.4
    assert pytest.approx(book_to_market, rel=1e-6) == 0.1


def test_get_portfolio_exposures_empty_returns_schema(
    mock_service: FactorExposureService,
) -> None:
    """Return an empty dataframe with schema when holdings are missing."""

    mock_service._get_portfolio_holdings = MagicMock(return_value=None)

    result = mock_service.get_portfolio_exposures(
        portfolio_id="global",
        start_date=date(2024, 1, 5),
        end_date=date(2024, 1, 5),
        factors=["momentum_12_1"],
    )

    assert result.exposures.is_empty()
    assert result.exposures.schema == {
        "date": pl.Date,
        "factor": pl.Utf8,
        "exposure": pl.Float64,
    }


def test_get_stock_exposures_returns_contributions(
    mock_service: FactorExposureService,
) -> None:
    """Ensure stock-level exposures include contribution and sorting."""

    holdings = pl.DataFrame(
        {
            "permno": [1, 2],
            "symbol": ["AAA", "BBB"],
            "weight": [0.6, 0.4],
        }
    )
    mock_service._get_portfolio_holdings = MagicMock(return_value=holdings)

    result = mock_service.get_stock_exposures(
        portfolio_id="global",
        factor="momentum_12_1",
        as_of_date=date(2024, 1, 5),
    )

    assert set(result.columns) == {"symbol", "weight", "exposure", "contribution"}
    assert result.height == 2
    assert result.row(0)[0] == "BBB"  # Higher contribution (0.4 * 2.0)


def test_get_portfolio_holdings_denies_without_permission(
    mock_factor_builder: MagicMock,
) -> None:
    """Verify _get_portfolio_holdings returns None when user lacks VIEW_ALL_POSITIONS."""

    service = FactorExposureService(
        factor_builder=mock_factor_builder,
        db_adapter=MagicMock(),  # db available
        redis_client=None,
        user={"role": "viewer", "user_id": "denied_user"},
    )

    with patch(
        "apps.web_console.services.factor_exposure_service.has_permission",
        return_value=False,
    ):
        result = service._get_portfolio_holdings(
            portfolio_id="global",
            as_of_date=date(2024, 1, 5),
        )

    assert result is None
