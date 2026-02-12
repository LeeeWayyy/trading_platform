"""Tests for LiveVsBacktestAnalyzer (P6T12.3)."""

from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from libs.analytics.live_vs_backtest import (
    AlertLevel,
    LiveVsBacktestAnalyzer,
    OverlayConfig,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_returns(start: date, returns: list[float]) -> pl.DataFrame:
    dates = [start + timedelta(days=i) for i in range(len(returns))]
    return pl.DataFrame(
        {"date": dates, "return": returns},
        schema={"date": pl.Date, "return": pl.Float64},
    )


# ===================================================================
# Basic Analysis
# ===================================================================
class TestBasicAnalysis:
    def test_identical_series(self) -> None:
        """Identical live and backtest should have zero divergence."""
        start = date(2024, 1, 1)
        returns = [0.01, 0.02, -0.005, 0.01, 0.005]
        df = _make_returns(start, returns)

        analyzer = LiveVsBacktestAnalyzer()
        result = analyzer.analyze(df, df)

        assert result.tracking_error_annualized == 0.0
        assert result.cumulative_divergence is not None
        assert result.cumulative_divergence < 1e-10
        assert result.alert_level == AlertLevel.NONE

    def test_divergent_series(self) -> None:
        """Different series should have non-zero TE."""
        start = date(2024, 1, 1)
        live = _make_returns(start, [0.01, 0.02, -0.01, 0.03, 0.00])
        bt = _make_returns(start, [0.02, 0.01, 0.00, 0.02, -0.01])

        analyzer = LiveVsBacktestAnalyzer()
        result = analyzer.analyze(live, bt)

        assert result.tracking_error_annualized is not None
        assert result.tracking_error_annualized > 0
        assert result.cumulative_divergence is not None

    def test_cumulative_curves_start_at_zero(self) -> None:
        """Both cumulative curves should start near zero."""
        start = date(2024, 1, 1)
        df = _make_returns(start, [0.01, 0.02, 0.01])

        analyzer = LiveVsBacktestAnalyzer()
        result = analyzer.analyze(df, df)

        # First cumulative value is 1 * (1+0.01) - 1 = 0.01
        assert result.live_cumulative["cumulative_return"][0] == pytest.approx(0.01)


# ===================================================================
# Date Alignment
# ===================================================================
class TestDateAlignment:
    def test_missing_live_dates_zero_filled(self) -> None:
        """Live dates missing within backtest range are zero-filled."""
        start = date(2024, 1, 1)
        bt = _make_returns(start, [0.01, 0.02, 0.03, 0.01, 0.02])
        # Live only has dates 0, 2, 4 (missing 1, 3)
        live = pl.DataFrame(
            {
                "date": [start, start + timedelta(days=2), start + timedelta(days=4)],
                "return": [0.01, 0.03, 0.02],
            },
            schema={"date": pl.Date, "return": pl.Float64},
        )

        analyzer = LiveVsBacktestAnalyzer()
        result = analyzer.analyze(live, bt)

        # Should have 5 dates in output (all backtest dates)
        assert len(result.live_cumulative) == 5
        assert len(result.backtest_cumulative) == 5

    def test_overlay_window_restriction(self) -> None:
        """Overlay window should restrict to specified range."""
        start = date(2024, 1, 1)
        bt = _make_returns(start, [0.01] * 30)
        live = _make_returns(start, [0.02] * 30)

        analyzer = LiveVsBacktestAnalyzer()
        result = analyzer.analyze(
            live, bt,
            overlay_start=start + timedelta(days=5),
            overlay_end=start + timedelta(days=15),
        )

        # Should have ~11 dates (day 5 through day 15)
        assert len(result.live_cumulative) == 11

    def test_no_overlap_live_zero_filled(self) -> None:
        """Non-overlapping live dates should be zero-filled (no trades = flat)."""
        bt = _make_returns(date(2024, 1, 1), [0.01, 0.02])
        live = _make_returns(date(2024, 6, 1), [0.01, 0.02])

        analyzer = LiveVsBacktestAnalyzer()
        result = analyzer.analyze(live, bt)

        # Live data doesn't overlap backtest dates, so live_return = 0.0
        # TE should be non-None since we have 2 backtest dates
        assert result.tracking_error_annualized is not None
        # Live cumulative should be flat (all zeros)
        assert result.live_cumulative["cumulative_return"][0] == pytest.approx(0.0)

    def test_overlay_window_outside_backtest_returns_insufficient(self) -> None:
        """Window entirely outside backtest range returns insufficient."""
        bt = _make_returns(date(2024, 1, 1), [0.01, 0.02])
        live = _make_returns(date(2024, 6, 1), [0.01, 0.02])

        analyzer = LiveVsBacktestAnalyzer()
        result = analyzer.analyze(
            live, bt,
            overlay_start=date(2024, 6, 1),
            overlay_end=date(2024, 6, 30),
        )

        assert result.tracking_error_annualized is None
        assert "no overlapping" in result.alert_message.lower()

    def test_empty_backtest_returns_insufficient(self) -> None:
        empty = pl.DataFrame(
            {"date": [], "return": []},
            schema={"date": pl.Date, "return": pl.Float64},
        )
        live = _make_returns(date(2024, 1, 1), [0.01])
        analyzer = LiveVsBacktestAnalyzer()
        result = analyzer.analyze(live, empty)
        assert result.tracking_error_annualized is None


# ===================================================================
# Alert Levels
# ===================================================================
class TestAlertLevels:
    def test_red_alert_large_divergence(self) -> None:
        """Large cumulative divergence should trigger RED."""
        start = date(2024, 1, 1)
        # bt flat, live grows 20% -> divergence > 10%
        bt = _make_returns(start, [0.0] * 30)
        live = _make_returns(start, [0.01] * 30)  # ~34% total

        config = OverlayConfig(divergence_threshold=0.10)
        analyzer = LiveVsBacktestAnalyzer(config)
        result = analyzer.analyze(live, bt)

        assert result.alert_level == AlertLevel.RED

    def test_yellow_alert_sustained_te(self) -> None:
        """Sustained high rolling TE should trigger YELLOW."""
        start = date(2024, 1, 1)
        n = 30
        # bt flat, live oscillates with high volatility
        bt = _make_returns(start, [0.0] * n)
        live_rets = [0.05 if i % 2 == 0 else -0.04 for i in range(n)]
        live = _make_returns(start, live_rets)

        config = OverlayConfig(
            tracking_error_threshold=0.01,  # Very low threshold
            consecutive_days_for_yellow=3,
            rolling_window_days=5,
            divergence_threshold=10.0,  # High to prevent RED
        )
        analyzer = LiveVsBacktestAnalyzer(config)
        result = analyzer.analyze(live, bt)

        assert result.alert_level == AlertLevel.YELLOW

    def test_none_alert_when_close(self) -> None:
        """Nearly identical series should be NONE."""
        start = date(2024, 1, 1)
        bt = _make_returns(start, [0.01] * 30)
        live = _make_returns(start, [0.01001] * 30)

        config = OverlayConfig(
            divergence_threshold=0.50,
            tracking_error_threshold=0.50,
        )
        analyzer = LiveVsBacktestAnalyzer(config)
        result = analyzer.analyze(live, bt)

        assert result.alert_level == AlertLevel.NONE

    def test_insufficient_data_for_yellow(self) -> None:
        """With < rolling_window_days, YELLOW cannot trigger."""
        start = date(2024, 1, 1)
        bt = _make_returns(start, [0.0, 0.0, 0.0])
        live = _make_returns(start, [0.01, 0.01, 0.01])

        config = OverlayConfig(
            rolling_window_days=20,
            divergence_threshold=10.0,  # Prevent RED
        )
        analyzer = LiveVsBacktestAnalyzer(config)
        result = analyzer.analyze(live, bt)

        # YELLOW needs 20 days, only 3 here
        assert result.alert_level == AlertLevel.NONE

    def test_red_overrides_yellow(self) -> None:
        """RED should take precedence over YELLOW."""
        start = date(2024, 1, 1)
        n = 30
        bt = _make_returns(start, [0.0] * n)
        live = _make_returns(start, [0.02] * n)  # Large divergence

        config = OverlayConfig(
            divergence_threshold=0.10,
            tracking_error_threshold=0.01,
            consecutive_days_for_yellow=1,
        )
        analyzer = LiveVsBacktestAnalyzer(config)
        result = analyzer.analyze(live, bt)

        assert result.alert_level == AlertLevel.RED  # Not YELLOW


# ===================================================================
# Divergence Start Date
# ===================================================================
class TestDivergenceStartDate:
    def test_divergence_detected(self) -> None:
        """Should detect when divergence exceeds threshold."""
        start = date(2024, 1, 1)
        n = 30
        bt = _make_returns(start, [0.0] * n)
        live = _make_returns(start, [0.02] * n)

        config = OverlayConfig(
            divergence_threshold=0.10,
            rolling_window_days=5,
        )
        analyzer = LiveVsBacktestAnalyzer(config)
        result = analyzer.analyze(live, bt)

        assert result.divergence_start_date is not None
        # Divergence should be detected within first ~10 days
        assert result.divergence_start_date <= start + timedelta(days=15)

    def test_no_divergence_returns_none(self) -> None:
        """No divergence means no start date."""
        start = date(2024, 1, 1)
        df = _make_returns(start, [0.01] * 10)

        analyzer = LiveVsBacktestAnalyzer(OverlayConfig(divergence_threshold=10.0))
        result = analyzer.analyze(df, df)

        assert result.divergence_start_date is None


# ===================================================================
# OverlayConfig
# ===================================================================
class TestOverlayConfig:
    def test_defaults(self) -> None:
        cfg = OverlayConfig()
        assert cfg.tracking_error_threshold == 0.05
        assert cfg.divergence_threshold == 0.10
        assert cfg.consecutive_days_for_yellow == 5
        assert cfg.rolling_window_days == 20

    def test_custom_values(self) -> None:
        cfg = OverlayConfig(tracking_error_threshold=0.10, rolling_window_days=30)
        assert cfg.tracking_error_threshold == 0.10
        assert cfg.rolling_window_days == 30


# ===================================================================
# NaN Handling
# ===================================================================
class TestNaNHandling:
    def test_nan_live_returns_filtered(self) -> None:
        """NaN values in live returns should be filtered out, not mask divergence."""
        start = date(2024, 1, 1)
        bt = _make_returns(start, [0.01, 0.02, 0.03, 0.01, 0.02])
        # Inject NaN into live returns
        live = pl.DataFrame(
            {
                "date": [start + timedelta(days=i) for i in range(5)],
                "return": [0.01, float("nan"), 0.03, float("nan"), 0.02],
            },
            schema={"date": pl.Date, "return": pl.Float64},
        )

        analyzer = LiveVsBacktestAnalyzer()
        result = analyzer.analyze(live, bt)

        # NaN dates should be filtered, leaving 3 aligned dates
        assert len(result.live_cumulative) == 3
        assert result.tracking_error_annualized is not None
        # Cumulative curves should contain no NaN
        cum_vals = result.live_cumulative["cumulative_return"].to_list()
        assert all(v == v for v in cum_vals)  # NaN != NaN

    def test_all_nan_live_returns_insufficient(self) -> None:
        """All-NaN live returns (after join) should produce insufficient result."""
        start = date(2024, 1, 1)
        bt = _make_returns(start, [0.01, 0.02])
        live = pl.DataFrame(
            {
                "date": [start, start + timedelta(days=1)],
                "return": [float("nan"), float("nan")],
            },
            schema={"date": pl.Date, "return": pl.Float64},
        )

        analyzer = LiveVsBacktestAnalyzer()
        result = analyzer.analyze(live, bt)

        # Both dates filtered → insufficient data
        assert result.tracking_error_annualized is None
        assert "Insufficient" in result.alert_message
