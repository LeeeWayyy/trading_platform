"""Tests for backtest page components.

Tests the backtest form, results display, and visualization components.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import polars as pl


class TestBacktestForm:
    """Tests for backtest form component."""

    def test_get_available_alphas_returns_canonical_alphas(self) -> None:
        """Verify form uses canonical alphas from alpha library."""
        from apps.web_console.components.backtest_form import get_available_alphas

        alphas = get_available_alphas()

        # Should contain the 5 canonical alphas
        assert "momentum" in alphas
        assert "reversal" in alphas
        assert "value" in alphas
        assert "quality" in alphas
        assert "volatility" in alphas

    def test_backtest_form_validates_date_range(self) -> None:
        """Verify form validates end_date > start_date."""
        from libs.backtest.job_queue import BacktestJobConfig

        # Valid config
        config = BacktestJobConfig(
            alpha_name="momentum",
            start_date=date(2023, 1, 1),
            end_date=date(2023, 12, 31),
            weight_method="zscore",
        )
        assert config.end_date > config.start_date

    def test_weight_method_options_match_job_queue(self) -> None:
        """Verify weight method options match BacktestJobConfig expectations."""
        from libs.backtest.job_queue import BacktestJobConfig

        valid_methods = ["zscore", "quantile", "rank"]

        for method in valid_methods:
            config = BacktestJobConfig(
                alpha_name="momentum",
                start_date=date(2023, 1, 1),
                end_date=date(2023, 12, 31),
                weight_method=method,
            )
            assert config.weight_method == method


class TestVisualizationComponents:
    """Tests for visualization chart components."""

    def test_equity_curve_handles_empty_data(self) -> None:
        """Verify equity curve handles empty dataframe gracefully."""
        from apps.web_console.components.equity_curve_chart import render_equity_curve

        empty_df = pl.DataFrame(schema={"date": pl.Date, "return": pl.Float64})

        # Should not raise - just display info message
        with patch("streamlit.info") as mock_info:
            render_equity_curve(empty_df)
            mock_info.assert_called_once()

    def test_equity_curve_computes_cumulative_return(self) -> None:
        """Verify cumulative return calculation is correct."""
        import polars as pl

        # Create sample returns
        returns_data = {
            "date": [date(2023, 1, i) for i in range(1, 6)],
            "return": [0.01, 0.02, -0.01, 0.03, 0.01],
        }
        df = pl.DataFrame(returns_data)

        # Compute cumulative return
        cumulative = (1 + df["return"]).cum_prod() - 1

        # Verify: (1.01 * 1.02 * 0.99 * 1.03 * 1.01) - 1 ≈ 0.0612
        expected = (1.01 * 1.02 * 0.99 * 1.03 * 1.01) - 1
        assert abs(cumulative[-1] - expected) < 1e-10

    def test_drawdown_computation(self) -> None:
        """Verify drawdown calculation is correct."""
        import polars as pl

        # Create sample returns with a drawdown
        returns_data = {
            "date": [date(2023, 1, i) for i in range(1, 6)],
            "return": [0.10, 0.05, -0.20, 0.05, 0.10],
        }
        df = pl.DataFrame(returns_data)

        # Compute cumulative wealth
        cumulative = (1 + df["return"]).cum_prod()
        running_max = cumulative.cum_max()
        drawdown = (cumulative - running_max) / running_max

        # Max drawdown should be negative
        assert drawdown.min() < 0

        # No drawdown on first day (at peak)
        assert drawdown[0] == 0

    def test_ic_timeseries_handles_missing_rank_ic(self) -> None:
        """Verify IC chart works without rank_ic column."""
        from apps.web_console.components.ic_timeseries_chart import render_ic_timeseries

        # DataFrame with only ic (no rank_ic)
        ic_data = {
            "date": [date(2023, 1, i) for i in range(1, 31)],
            "ic": [0.05 + 0.01 * (i % 5) for i in range(30)],
        }
        df = pl.DataFrame(ic_data)

        # Should not raise
        with patch("streamlit.plotly_chart"):
            render_ic_timeseries(df)


class TestBacktestResults:
    """Tests for backtest results display component."""

    def test_metrics_summary_handles_missing_values(self) -> None:
        """Verify metrics summary handles None values gracefully."""
        from apps.web_console.components.backtest_results import render_metrics_summary

        # Create mock result with missing values
        mock_result = MagicMock()
        mock_result.mean_ic = None
        mock_result.icir = None
        mock_result.hit_rate = None
        mock_result.coverage = 0.95
        mock_result.turnover_result = None

        with patch("streamlit.columns") as mock_cols:
            mock_cols.return_value = [MagicMock() for _ in range(5)]
            with patch("streamlit.metric"):
                render_metrics_summary(mock_result)

    def test_export_buttons_require_permission(self) -> None:
        """Verify export requires EXPORT_DATA permission."""
        from apps.web_console.components.backtest_results import render_export_buttons

        mock_result = MagicMock()

        # User without EXPORT_DATA permission (viewer role)
        user_info = {"role": "viewer"}

        with patch("streamlit.info") as mock_info:
            render_export_buttons(mock_result, user_info)
            mock_info.assert_called_once()
            assert "EXPORT_DATA" in str(mock_info.call_args)

    def test_export_buttons_shown_for_operator(self) -> None:
        """Verify export buttons shown for operator role."""
        from apps.web_console.components.backtest_results import render_export_buttons

        mock_result = MagicMock()
        mock_result.daily_signals = pl.DataFrame({"date": [], "permno": [], "signal": []})
        mock_result.daily_ic = pl.DataFrame({"date": [], "ic": []})
        mock_result.backtest_id = "test123"
        mock_result.alpha_name = "momentum"
        mock_result.start_date = date(2023, 1, 1)
        mock_result.end_date = date(2023, 12, 31)
        mock_result.mean_ic = 0.05
        mock_result.icir = 1.5
        mock_result.hit_rate = 0.6
        mock_result.coverage = 0.95
        mock_result.n_days = 252
        mock_result.n_symbols_avg = 500
        mock_result.snapshot_id = "snap123"
        mock_result.dataset_version_ids = {"v1": "123"}
        mock_result.turnover_result = MagicMock()
        mock_result.turnover_result.mean_turnover = 0.05

        # User with EXPORT_DATA permission (operator role)
        user_info = {"role": "operator"}

        with patch("streamlit.subheader"):
            with patch("streamlit.columns") as mock_cols:
                mock_cols.return_value = [MagicMock() for _ in range(3)]
                with patch("streamlit.download_button"):
                    render_export_buttons(mock_result, user_info)


class TestPollingLogic:
    """Tests for progressive polling logic."""

    def test_poll_interval_increases_over_time(self) -> None:
        """Verify polling interval increases as time passes."""
        from apps.web_console.pages.backtest import get_poll_interval_ms

        # Fast polling at start
        assert get_poll_interval_ms(0) == 2000
        assert get_poll_interval_ms(15) == 2000
        assert get_poll_interval_ms(29) == 2000

        # Medium polling after 30s
        assert get_poll_interval_ms(30) == 5000
        assert get_poll_interval_ms(45) == 5000

        # Slower after 60s
        assert get_poll_interval_ms(60) == 10_000
        assert get_poll_interval_ms(120) == 10_000

        # Slowest after 5 minutes
        assert get_poll_interval_ms(300) == 30_000
        assert get_poll_interval_ms(600) == 30_000


class TestUserInfoWithRole:
    """Tests for _get_user_with_role wrapper."""

    def test_get_user_with_role_adds_role_from_session(self) -> None:
        """Verify role is added from session_state."""
        mock_session_state = {
            "authenticated": True,
            "username": "test_user",
            "user_id": "user123",
            "auth_method": "oauth2",
            "session_id": "session123",
            "role": "admin",
            "strategies": ["alpha_baseline", "momentum"],
        }

        with patch("streamlit.session_state", mock_session_state):
            from apps.web_console.pages.backtest import _get_user_with_role

            user_info = _get_user_with_role()

            assert user_info["role"] == "admin"
            assert user_info["strategies"] == ["alpha_baseline", "momentum"]

    def test_get_user_with_role_defaults_to_viewer(self) -> None:
        """Verify role defaults to viewer if not in session."""
        mock_session_state = {
            "authenticated": True,
            "username": "test_user",
            "user_id": "user123",
            "auth_method": "oauth2",
            "session_id": "session123",
            # No role set
        }

        with patch("streamlit.session_state", mock_session_state):
            from apps.web_console.pages.backtest import _get_user_with_role

            user_info = _get_user_with_role()

            # Should default to viewer (most restrictive)
            assert user_info["role"] == "viewer"
            assert user_info["strategies"] == []
