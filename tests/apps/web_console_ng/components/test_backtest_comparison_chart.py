"""Tests for backtest_comparison_chart component (P6T12.2).

Tests metric computation, date alignment, color-coding logic, and
build_comparison_metrics helper.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import Any

import polars as pl
import pytest

from apps.web_console_ng.components.backtest_comparison_chart import (
    HIGHER_IS_BETTER,
    build_comparison_metrics,
    compute_max_drawdown,
    compute_sharpe,
    compute_total_return,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_returns(start: date, returns: list[float]) -> pl.DataFrame:
    dates = [start + timedelta(days=i) for i in range(len(returns))]
    return pl.DataFrame({"date": dates, "return": returns})


# ===================================================================
# compute_max_drawdown
# ===================================================================
class TestComputeMaxDrawdown:
    def test_no_drawdown(self) -> None:
        """Monotonically increasing series has zero drawdown."""
        result = compute_max_drawdown([0.01, 0.02, 0.01, 0.03])
        assert result is not None
        assert result == 0.0

    def test_known_drawdown(self) -> None:
        """10% rise followed by ~9.09% drop."""
        # equity: 1.0, 1.1, 1.0 → dd = (1.0/1.1) - 1 = -0.0909
        result = compute_max_drawdown([0.10, -0.10 / 1.10])
        assert result is not None
        # Actually: 0.10 rise -> eq = 1.10; then -0.10/1.10 = -0.0909 -> eq = 1.10 * (1 - 0.0909) = 1.0
        # So DD = 1.0/1.1 - 1 = -0.0909. Max dd = 0.0909
        assert abs(result - (1.0 - 1.0 / 1.10)) < 1e-10

    def test_complete_drawdown(self) -> None:
        """100% loss."""
        result = compute_max_drawdown([0.10, -1.0])
        assert result is not None
        assert abs(result - 1.0) < 1e-10

    def test_empty_returns_none(self) -> None:
        assert compute_max_drawdown([]) is None


# ===================================================================
# compute_total_return
# ===================================================================
class TestComputeTotalReturn:
    def test_zero_returns(self) -> None:
        result = compute_total_return([0.0, 0.0, 0.0])
        assert result is not None
        assert abs(result) < 1e-10

    def test_known_return(self) -> None:
        # (1+0.1) * (1+0.2) - 1 = 0.32
        result = compute_total_return([0.10, 0.20])
        assert result is not None
        assert abs(result - 0.32) < 1e-10

    def test_empty_returns_none(self) -> None:
        assert compute_total_return([]) is None


# ===================================================================
# compute_sharpe
# ===================================================================
class TestComputeSharpe:
    def test_known_sharpe(self) -> None:
        """Verify against hand calculation."""
        returns = [0.01, 0.02, 0.01, 0.02, 0.01]
        mean_r = sum(returns) / 5
        var = sum((r - mean_r) ** 2 for r in returns) / 4
        expected = (mean_r / math.sqrt(var)) * math.sqrt(252)

        result = compute_sharpe(returns)
        assert result is not None
        assert abs(result - expected) < 1e-10

    def test_single_observation_returns_none(self) -> None:
        assert compute_sharpe([0.01]) is None

    def test_constant_returns_none(self) -> None:
        """Zero std should return None."""
        assert compute_sharpe([0.01, 0.01, 0.01]) is None

    def test_empty_returns_none(self) -> None:
        assert compute_sharpe([]) is None


# ===================================================================
# HIGHER_IS_BETTER directionality
# ===================================================================
class TestMetricDirectionality:
    def test_higher_is_better_metrics(self) -> None:
        for key in ("Mean IC", "ICIR", "Hit Rate", "Coverage", "Total Return", "Sharpe"):
            assert HIGHER_IS_BETTER[key] is True, f"{key} should be higher-is-better"

    def test_lower_is_better_metrics(self) -> None:
        for key in ("Max Drawdown", "Avg Turnover", "Total Cost"):
            assert HIGHER_IS_BETTER[key] is False, f"{key} should be lower-is-better"


# ===================================================================
# build_comparison_metrics
# ===================================================================
class TestBuildComparisonMetrics:
    @pytest.fixture()
    def sample_job(self) -> dict[str, Any]:
        return {
            "job_id": "job-1",
            "alpha_name": "momentum_1m",
            "start_date": "2024-01-01",
            "end_date": "2024-12-31",
            "mean_ic": 0.05,
            "icir": 1.2,
            "hit_rate": 0.55,
            "coverage": 0.98,
            "average_turnover": 0.15,
            "cost_summary": None,
        }

    def test_db_summary_fields(self, sample_job: dict[str, Any]) -> None:
        m = build_comparison_metrics(
            job=sample_job,
            label="Test",
            return_series=None,
            cost_summary_raw=None,
            basis="gross",
        )
        assert m["label"] == "Test"
        assert m["Mean IC"] == 0.05
        assert m["ICIR"] == 1.2
        assert m["Hit Rate"] == 0.55
        assert m["Coverage"] == 0.98
        assert m["Avg Turnover"] == 0.15
        assert m["Total Cost"] is None

    def test_computed_from_return_series(self, sample_job: dict[str, Any]) -> None:
        returns = [0.01, 0.02, -0.005, 0.015, 0.01]
        m = build_comparison_metrics(
            job=sample_job,
            label="Test",
            return_series=returns,
            cost_summary_raw=None,
            basis="gross",
        )
        # Should compute Total Return, Sharpe, Max Drawdown from series
        assert m["Total Return"] is not None
        assert m["Sharpe"] is not None
        assert m["Max Drawdown"] is not None

    def test_cost_summary_net_basis(self, sample_job: dict[str, Any]) -> None:
        cost_raw = {
            "total_net_return": 0.25,
            "net_sharpe": 1.5,
            "net_max_drawdown": 0.10,
            "total_cost_usd": 5000.0,
            "total_gross_return": 0.30,
            "total_cost_drag": 0.05,
            "commission_spread_cost_usd": 3000.0,
            "market_impact_cost_usd": 2000.0,
            "num_trades": 100,
            "avg_trade_cost_bps": 2.5,
        }
        m = build_comparison_metrics(
            job=sample_job,
            label="Net Test",
            return_series=None,
            cost_summary_raw=cost_raw,
            basis="net",
        )
        assert m["Total Return"] == 0.25
        assert m["Sharpe"] == 1.5
        assert m["Max Drawdown"] == 0.10
        assert m["Total Cost"] == 5000.0

    def test_cost_summary_gross_basis(self, sample_job: dict[str, Any]) -> None:
        cost_raw = {
            "total_gross_return": 0.30,
            "gross_sharpe": 2.0,
            "gross_max_drawdown": 0.08,
            "total_cost_usd": 5000.0,
            "total_cost_drag": 0.05,
            "commission_spread_cost_usd": 3000.0,
            "market_impact_cost_usd": 2000.0,
            "num_trades": 100,
            "avg_trade_cost_bps": 2.5,
        }
        m = build_comparison_metrics(
            job=sample_job,
            label="Gross Test",
            return_series=None,
            cost_summary_raw=cost_raw,
            basis="gross",
        )
        assert m["Total Return"] == 0.30
        assert m["Sharpe"] == 2.0
        assert m["Max Drawdown"] == 0.08

    def test_missing_cost_usd_key_shows_none(self, sample_job: dict[str, Any]) -> None:
        """When total_cost_usd is missing from raw dict, show N/A."""
        cost_raw = {
            "total_net_return": 0.25,
            "total_cost_drag": 0.05,
            "commission_spread_cost_usd": 3000.0,
            "market_impact_cost_usd": 2000.0,
            "num_trades": 100,
            "avg_trade_cost_bps": 2.5,
            # No total_cost_usd key
        }
        m = build_comparison_metrics(
            job=sample_job,
            label="Test",
            return_series=None,
            cost_summary_raw=cost_raw,
            basis="net",
        )
        assert m["Total Cost"] is None

    def test_corrupt_cost_summary_fallback(self, sample_job: dict[str, Any]) -> None:
        """Corrupt cost_summary should fall back to return series."""
        returns = [0.01, 0.02, 0.03]
        m = build_comparison_metrics(
            job=sample_job,
            label="Test",
            return_series=returns,
            cost_summary_raw={"invalid": "data"},
            basis="net",
        )
        # Should compute from return series since CostSummary.from_dict fails
        assert m["Total Return"] is not None


# ===================================================================
# Net/Gross basis selection
# ===================================================================
class TestBasisSelection:
    def test_all_net_uses_net(self) -> None:
        """When cost_summary has net data and basis=net, use net values."""
        job: dict[str, Any] = {
            "mean_ic": 0.05, "icir": 1.0, "hit_rate": 0.5,
            "coverage": 0.9, "average_turnover": 0.1,
        }
        cost_raw = {
            "total_net_return": 0.20,
            "net_sharpe": 1.8,
            "net_max_drawdown": 0.07,
            "total_gross_return": 0.25,
            "gross_sharpe": 2.0,
            "gross_max_drawdown": 0.06,
            "total_cost_usd": 1000.0,
            "total_cost_drag": 0.05,
            "commission_spread_cost_usd": 500.0,
            "market_impact_cost_usd": 500.0,
            "num_trades": 50,
            "avg_trade_cost_bps": 1.0,
        }
        m = build_comparison_metrics(
            job=job, label="T", return_series=None,
            cost_summary_raw=cost_raw, basis="net",
        )
        assert m["Total Return"] == 0.20
        assert m["Sharpe"] == 1.8

    def test_gross_basis_uses_gross(self) -> None:
        job: dict[str, Any] = {
            "mean_ic": 0.05, "icir": 1.0, "hit_rate": 0.5,
            "coverage": 0.9, "average_turnover": 0.1,
        }
        cost_raw = {
            "total_net_return": 0.20,
            "net_sharpe": 1.8,
            "net_max_drawdown": 0.07,
            "total_gross_return": 0.25,
            "gross_sharpe": 2.0,
            "gross_max_drawdown": 0.06,
            "total_cost_usd": 1000.0,
            "total_cost_drag": 0.05,
            "commission_spread_cost_usd": 500.0,
            "market_impact_cost_usd": 500.0,
            "num_trades": 50,
            "avg_trade_cost_bps": 1.0,
        }
        m = build_comparison_metrics(
            job=job, label="T", return_series=None,
            cost_summary_raw=cost_raw, basis="gross",
        )
        assert m["Total Return"] == 0.25
        assert m["Sharpe"] == 2.0
