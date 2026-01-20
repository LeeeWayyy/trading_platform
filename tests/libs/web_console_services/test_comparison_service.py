"""Unit tests for libs.web_console_services.comparison_service."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

import pandas as pd
import pytest

from libs.web_console_services.comparison_service import ComparisonService


def test_to_pnl_frame_defaults_missing_daily_pnl_and_sorts() -> None:
    rows = [
        {"strategy_id": "s1", "trade_date": date(2025, 1, 2)},
        {"strategy_id": "s1", "trade_date": date(2025, 1, 1), "daily_pnl": 5.0},
    ]

    frame = ComparisonService._to_pnl_frame(rows, ["s1"])

    assert list(frame.index.date) == [date(2025, 1, 1), date(2025, 1, 2)]
    assert frame.loc[pd.Timestamp("2025-01-02"), "s1"] == 0.0


def test_build_equity_curves_returns_cumulative_series() -> None:
    pnl_frame = pd.DataFrame(
        {"s1": [1.0, -2.0, 3.0]},
        index=[pd.Timestamp("2025-01-01"), pd.Timestamp("2025-01-02"), pd.Timestamp("2025-01-03")],
    )

    curves = ComparisonService._build_equity_curves(pnl_frame)

    assert curves[0]["strategy_id"] == "s1"
    equity_values = [point["equity"] for point in curves[0]["equity"]]
    assert equity_values == [1.0, -1.0, 2.0]


def test_compute_metrics_handles_single_point() -> None:
    pnl_frame = pd.DataFrame(
        {"s1": [5.0]},
        index=[pd.Timestamp("2025-01-01")],
    )
    service = ComparisonService(scoped_access=None)  # type: ignore[arg-type]

    metrics = service._compute_metrics(pnl_frame)

    assert metrics["s1"]["total_return"] == 5.0
    assert metrics["s1"]["volatility"] == 0.0
    assert metrics["s1"]["sharpe"] == 0.0


def test_compute_correlation_matrix_single_strategy_empty() -> None:
    pnl_frame = pd.DataFrame({"s1": [1.0, 2.0]}, index=[1, 2])

    corr = ComparisonService.compute_correlation_matrix(pnl_frame)

    assert corr.empty


def test_compute_combined_portfolio_rejects_invalid_weights() -> None:
    service = ComparisonService(scoped_access=None)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="sum to 1.0"):
        service.compute_combined_portfolio({"s1": 0.7, "s2": 0.2}, pnl_data=[])


@pytest.mark.asyncio()
async def test_get_comparison_data_empty_strategy_ids() -> None:
    scoped_access = AsyncMock()
    scoped_access.get_pnl_summary = AsyncMock()

    service = ComparisonService(scoped_access)
    data = await service.get_comparison_data([], date(2025, 1, 1), date(2025, 1, 2))

    assert data["metrics"] == {}
    scoped_access.get_pnl_summary.assert_not_awaited()


@pytest.mark.asyncio()
async def test_get_comparison_data_rejects_bad_date_range() -> None:
    service = ComparisonService(scoped_access=AsyncMock())

    with pytest.raises(ValueError, match="date_from cannot be after date_to"):
        await service.get_comparison_data(["s1"], date(2025, 1, 2), date(2025, 1, 1))
