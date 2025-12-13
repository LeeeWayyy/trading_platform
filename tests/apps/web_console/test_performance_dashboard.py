"""Tests for Performance Dashboard Streamlit page and chart helpers."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from apps.web_console.pages import performance as perf_page
from apps.web_console.components import pnl_chart


@pytest.fixture(autouse=True)
def mock_streamlit(monkeypatch):
    """Replace streamlit module used in performance page with no-op stubs."""

    class DummySpinner:
        def __enter__(self):
            return None

        def __exit__(self, exc_type, exc, tb):
            return False

    class DummyStreamlit:
        def __init__(self):
            self._metrics = []
            self._infos: list[str] = []
            self._warnings: list[str] = []
            self._errors: list[str] = []
            self._captions: list[str] = []
            self._dataframes: list[Any] = []
            self._date_input_value: Any = (date.today(), date.today())
            self.session_state: dict[str, Any] = {}

        def subheader(self, *_args, **_kwargs):
            return None

        def error(self, *_args, **_kwargs):
            self._errors.append(str(_args[0]) if _args else "")
            return None

        def info(self, *_args, **_kwargs):
            self._infos.append(str(_args[0]) if _args else "")
            return None

        def warning(self, *_args, **_kwargs):
            self._warnings.append(str(_args[0]) if _args else "")
            return None

        def caption(self, *_args, **_kwargs):
            self._captions.append(str(_args[0]) if _args else "")
            return None

        def metric(self, label, value, delta=None):
            self._metrics.append((label, value, delta))
            return None

        def columns(self, n):
            # Return list of objects with metric method
            return [self for _ in range(n)]

        def button(self, *_args, **_kwargs):
            return False

        def dataframe(self, *_args, **_kwargs):
            self._dataframes.append(_args[0] if _args else {})
            return None

        def spinner(self, *_args, **_kwargs):
            return DummySpinner()

        def divider(self):
            return None

        def set_page_config(self, *_args, **_kwargs):
            return None

        def title(self, *_args, **_kwargs):
            return None

        def date_input(self, *_args, **_kwargs):
            return self._date_input_value

        def stop(self):
            # mimic Streamlit's stop; raising SystemExit keeps flow deterministic in tests
            raise SystemExit()

    dummy = DummyStreamlit()
    monkeypatch.setattr(perf_page, "st", dummy)
    yield dummy


class TestPerformancePage:
    def test_render_with_data(self, monkeypatch):
        fake_pnl = {
            "positions": [
                {
                    "symbol": "AAPL",
                    "qty": 10,
                    "avg_entry_price": 100,
                    "current_price": 110,
                    "unrealized_pl": 100,
                    "unrealized_pl_pct": 10,
                    "price_source": "real-time",
                }
            ],
            "total_unrealized_pl": 100,
            "total_unrealized_pl_pct": 10,
        }
        fake_positions = {"positions": ["AAPL"], "total_positions": 1}
        fake_performance = {
            "daily_pnl": [
                {
                    "date": "2024-01-01",
                    "realized_pl": "10",
                    "cumulative_realized_pl": "10",
                    "peak_equity": "10",
                    "drawdown_pct": "0",
                    "closing_trade_count": 1,
                }
            ],
            "total_realized_pl": "10",
            "max_drawdown_pct": "0",
            "start_date": "2024-01-01",
            "end_date": "2024-01-02",
            "data_available_from": "2024-01-01",
        }

        monkeypatch.setattr(perf_page, "fetch_realtime_pnl", lambda: fake_pnl)
        monkeypatch.setattr(perf_page, "fetch_positions", lambda: fake_positions)
        monkeypatch.setattr(
            perf_page,
            "fetch_performance",
            lambda start, end, strategies, user_id: fake_performance,
        )

        # Streamlit rendering can't be asserted easily; ensure no exceptions are raised
        perf_page.render_realtime_pnl()
        perf_page.render_position_summary()
        perf_page.render_historical_performance(date(2024, 1, 1), date(2024, 1, 2), ["s1"])

    def test_render_no_data(self, monkeypatch):
        monkeypatch.setattr(perf_page, "fetch_realtime_pnl", lambda: {"positions": [], "total_unrealized_pl": 0})
        monkeypatch.setattr(perf_page, "fetch_positions", lambda: {"positions": []})
        monkeypatch.setattr(
            perf_page,
            "fetch_performance",
            lambda start, end, strategies, user_id: {
                "daily_pnl": [],
                "total_realized_pl": "0",
                "max_drawdown_pct": "0",
                "start_date": "2024-01-01",
                "end_date": "2024-01-02",
                "data_available_from": None,
            },
        )

        perf_page.render_realtime_pnl()
        perf_page.render_position_summary()
        perf_page.render_historical_performance(date(2024, 1, 1), date(2024, 1, 2), ["s1"])

    def test_invalid_range_error(self):
        # End before start triggers error branch
        perf_page.render_historical_performance(date(2024, 2, 2), date(2024, 1, 1), ["s1"])
        st_obj = perf_page.st  # type: ignore[attr-defined]
        assert any("exceed" in msg for msg in st_obj._errors)

    def test_date_input_stop_on_invalid_range(self, monkeypatch):
        # simulate inverted range from date_input
        st_obj = perf_page.st  # type: ignore[attr-defined]
        st_obj._date_input_value = (date(2024, 2, 2), date(2024, 1, 1))
        with pytest.raises(SystemExit):
            perf_page._date_inputs()

    def test_date_input_single_date(self, monkeypatch):
        st_obj = perf_page.st  # type: ignore[attr-defined]
        st_obj._date_input_value = date(2024, 1, 5)
        start, end = perf_page._date_inputs()
        assert start == end == date(2024, 1, 5)

    def test_historical_performance_requires_auth(self, monkeypatch):
        """Ensure unauthenticated users see auth error and fetch_performance is not called."""
        # Return empty user (no user_id) to simulate unauthenticated state
        monkeypatch.setattr(perf_page, "_safe_current_user", lambda: {})

        # Track if fetch_performance is called
        fetch_called = []
        original_fetch = perf_page.fetch_performance

        def mock_fetch(*args, **kwargs):
            fetch_called.append(True)
            return original_fetch(*args, **kwargs)

        monkeypatch.setattr(perf_page, "fetch_performance", mock_fetch)

        perf_page.render_historical_performance(date(2024, 1, 1), date(2024, 1, 2), ["s1"])

        st_obj = perf_page.st  # type: ignore[attr-defined]
        # Should show authentication error
        assert any("Authentication required" in msg for msg in st_obj._errors)
        # fetch_performance should NOT be called
        assert len(fetch_called) == 0


class TestPnLCharts:
    def test_equity_curve_handles_empty(self):
        fig = pnl_chart.render_equity_curve([])
        assert fig is None

    def test_drawdown_chart_handles_empty(self):
        fig = pnl_chart.render_drawdown_chart([])
        assert fig is None

    def test_equity_curve_has_trace(self):
        daily = [
            {
                "date": "2024-01-01",
                "cumulative_realized_pl": "10",
                "realized_pl": "10",
                "peak_equity": "10",
                "drawdown_pct": "0",
                "closing_trade_count": 1,
            },
            {
                "date": "2024-01-02",
                "cumulative_realized_pl": "20",
                "realized_pl": "10",
                "peak_equity": "20",
                "drawdown_pct": "0",
                "closing_trade_count": 1,
            },
        ]
        fig = pnl_chart.render_equity_curve(daily)
        assert fig is not None
        assert len(fig.data) == 1

    def test_drawdown_chart_has_trace(self):
        daily = [
            {
                "date": "2024-01-01",
                "cumulative_realized_pl": "10",
                "realized_pl": "10",
                "peak_equity": "10",
                "drawdown_pct": "0",
                "closing_trade_count": 1,
            },
            {
                "date": "2024-01-02",
                "cumulative_realized_pl": "5",
                "realized_pl": "-5",
                "peak_equity": "10",
                "drawdown_pct": "-50",
                "closing_trade_count": 1,
            },
        ]
        fig = pnl_chart.render_drawdown_chart(daily)
        assert fig is not None
        assert len(fig.data) == 1
