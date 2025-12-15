from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from apps.web_console.services.comparison_service import ComparisonService


@pytest.fixture()
def sample_pnl() -> list[dict]:
    return [
        {"strategy_id": "s1", "trade_date": date(2025, 1, 1), "daily_pnl": 10},
        {"strategy_id": "s1", "trade_date": date(2025, 1, 2), "daily_pnl": -5},
        {"strategy_id": "s2", "trade_date": date(2025, 1, 1), "daily_pnl": 8},
        {"strategy_id": "s2", "trade_date": date(2025, 1, 2), "daily_pnl": 4},
    ]


def test_validate_weights_bounds() -> None:
    valid, msg = ComparisonService.validate_weights({"s1": 0.6, "s2": 0.4})
    assert valid
    assert msg == ""

    invalid, msg = ComparisonService.validate_weights({"s1": -0.1, "s2": 1.1})
    assert not invalid
    assert "between 0 and 1" in msg

    invalid_sum, msg = ComparisonService.validate_weights({"s1": 0.6, "s2": 0.6})
    assert not invalid_sum
    assert "sum to 1.0" in msg


def test_compute_correlation_matrix(sample_pnl: list[dict]) -> None:
    corr = ComparisonService.compute_correlation_matrix(sample_pnl)
    assert isinstance(corr, pd.DataFrame)
    assert set(corr.columns) == {"s1", "s2"}
    assert corr.loc["s1", "s1"] == 1.0


def test_compute_combined_portfolio(sample_pnl: list[dict]) -> None:
    service = ComparisonService(scoped_access=None)  # type: ignore[arg-type]
    weights = {"s1": 0.5, "s2": 0.5}
    combined = service.compute_combined_portfolio(weights, sample_pnl)

    assert combined["weights"] == weights
    assert combined["equity_curve"]
    assert combined["total_return"] == pytest.approx(8.5)  # (10-5)/2 + (8+4)/2


@pytest.mark.asyncio()
async def test_get_comparison_data(sample_pnl: list[dict]) -> None:
    scoped_access = AsyncMock()
    scoped_access.get_pnl_summary = AsyncMock(return_value=sample_pnl)
    scoped_access.authorized_strategies = ["s1", "s2"]

    service = ComparisonService(scoped_access)
    data = await service.get_comparison_data(["s1", "s2"], date(2025, 1, 1), date(2025, 1, 2))

    assert data["metrics"]["s1"]["total_return"] == 5
    assert not data["correlation_matrix"].empty
    assert data["combined_portfolio"]["equity_curve"]


@pytest.mark.asyncio()
async def test_get_comparison_data_calculates_limit() -> None:
    """Verify limit is calculated based on date range and AUTHORIZED strategy count."""
    scoped_access = AsyncMock()
    scoped_access.get_pnl_summary = AsyncMock(return_value=[])
    # User is authorized for 4 strategies but only selects 2
    scoped_access.authorized_strategies = ["s1", "s2", "s3", "s4"]

    service = ComparisonService(scoped_access)
    # 30 days * 4 authorized strategies = 120 rows needed (not 60 based on selected)
    await service.get_comparison_data(["s1", "s2"], date(2025, 1, 1), date(2025, 1, 30))

    # Verify get_pnl_summary was called with limit based on authorized count
    call_args = scoped_access.get_pnl_summary.call_args
    assert call_args.kwargs.get("limit") == 120


@pytest.mark.asyncio()
async def test_get_comparison_data_truncation_warning() -> None:
    """Verify truncation warning is set when date range exceeds limit."""
    scoped_access = AsyncMock()
    scoped_access.get_pnl_summary = AsyncMock(return_value=[])
    # User is authorized for 4 strategies
    scoped_access.authorized_strategies = ["s1", "s2", "s3", "s4"]

    service = ComparisonService(scoped_access)
    # 4 authorized strategies * 1500 days = 6000 rows, exceeds MAX_LIMIT of 5000
    data = await service.get_comparison_data(
        ["s1", "s2", "s3", "s4"], date(2021, 1, 1), date(2025, 2, 14)
    )

    assert data["truncation_warning"] is not None
    assert "rows" in data["truncation_warning"]
    assert "5000" in data["truncation_warning"]


@pytest.mark.asyncio()
async def test_get_comparison_data_no_truncation_warning_within_limit() -> None:
    """Verify no truncation warning when within limit."""
    scoped_access = AsyncMock()
    scoped_access.get_pnl_summary = AsyncMock(return_value=[])
    scoped_access.authorized_strategies = ["s1", "s2"]

    service = ComparisonService(scoped_access)
    # 2 authorized strategies * 30 days = 60 rows, well within limit
    data = await service.get_comparison_data(["s1", "s2"], date(2025, 1, 1), date(2025, 1, 30))

    assert data["truncation_warning"] is None


def test_compute_combined_portfolio_handles_missing_strategy() -> None:
    """Verify missing strategy columns are filled with 0 (no KeyError)."""
    service = ComparisonService(scoped_access=None)  # type: ignore[arg-type]
    # s3 has no P&L data in the input
    pnl_data = [
        {"strategy_id": "s1", "trade_date": date(2025, 1, 1), "daily_pnl": 10},
        {"strategy_id": "s2", "trade_date": date(2025, 1, 1), "daily_pnl": 5},
    ]
    weights = {"s1": 0.5, "s2": 0.3, "s3": 0.2}  # s3 missing from data

    combined = service.compute_combined_portfolio(weights, pnl_data)

    # Should not raise KeyError; s3 treated as 0 P&L
    assert combined["equity_curve"]
    # Total: 10*0.5 + 5*0.3 + 0*0.2 = 6.5
    assert combined["total_return"] == pytest.approx(6.5)


@pytest.mark.asyncio()
async def test_get_comparison_data_raises_on_zero_authorized_strategies() -> None:
    """Verify PermissionError raised when user has no authorized strategies."""
    scoped_access = AsyncMock()
    scoped_access.authorized_strategies = []  # No authorized strategies

    service = ComparisonService(scoped_access)

    with pytest.raises(PermissionError, match="No authorized strategies"):
        await service.get_comparison_data(["s1", "s2"], date(2025, 1, 1), date(2025, 1, 30))
