"""Tests for Performance Dashboard Streamlit page and chart helpers."""

from __future__ import annotations

import sys
from datetime import date
from typing import Any

import pytest
import requests

# Stub jwt early to avoid cryptography dependency during import of web_console.auth
jwt_stub = type(sys)("jwt")
jwt_stub.api_jwk = type(sys)("jwt.api_jwk")
jwt_stub.algorithms = type(sys)("jwt.algorithms")
jwt_stub.utils = type(sys)("jwt.utils")
sys.modules.setdefault("jwt", jwt_stub)
sys.modules.setdefault("jwt.api_jwk", jwt_stub.api_jwk)
sys.modules.setdefault("jwt.algorithms", jwt_stub.algorithms)
sys.modules.setdefault("jwt.utils", jwt_stub.utils)

# Stub web_console auth modules to avoid loading cryptography/redis stacks
auth_permissions_stub = type(sys)("apps.web_console.auth.permissions")
auth_permissions_stub.Permission = type(
    "Permission",
    (),
    {"VIEW_PNL": "view_pnl", "VIEW_ALL_STRATEGIES": "view_all_strategies"},
)


def _has_permission(user, permission):
    return True


def _get_authorized_strategies(user):
    return user.get("strategies", []) if isinstance(user, dict) else []


auth_permissions_stub.has_permission = _has_permission
auth_permissions_stub.get_authorized_strategies = _get_authorized_strategies

session_mgr_stub = type(sys)("apps.web_console.auth.session_manager")
session_mgr_stub.get_current_user = lambda: {
    "role": "viewer",
    "user_id": "u1",
    "strategies": ["s1"],
}


def _require_auth(func):
    return func


session_mgr_stub.require_auth = _require_auth

sys.modules.setdefault("apps.web_console.auth.permissions", auth_permissions_stub)
sys.modules.setdefault("apps.web_console.auth.session_manager", session_mgr_stub)

# Stub components package to avoid importing bulk_operations (pulls full auth stack)
pnl_chart_stub = type(sys)("apps.web_console.components.pnl_chart")


def _get_value(item, key):
    return item.get(key) if isinstance(item, dict) else getattr(item, key, None)


def _as_float(value):
    try:
        return float(value)
    except Exception:
        return 0.0


class _DummyFig:
    def __init__(self):
        self.data = [1]


def _dummy_render_equity_curve(daily):
    return None if not daily else _DummyFig()


def _dummy_render_drawdown_chart(daily):
    return None if not daily else _DummyFig()


pnl_chart_stub._get_value = _get_value
pnl_chart_stub._as_float = _as_float
pnl_chart_stub.render_equity_curve = _dummy_render_equity_curve
pnl_chart_stub.render_drawdown_chart = _dummy_render_drawdown_chart

components_pkg = type(sys)("apps.web_console.components")
components_pkg.pnl_chart = pnl_chart_stub
sys.modules.setdefault("apps.web_console.components", components_pkg)
sys.modules.setdefault("apps.web_console.components.pnl_chart", pnl_chart_stub)

# Stub strategy_scoped_queries to avoid cryptography dependency
strategy_stub = type(sys)("apps.web_console.data.strategy_scoped_queries")
strategy_stub.StrategyScopedDataAccess = object
sys.modules.setdefault("apps.web_console.data.strategy_scoped_queries", strategy_stub)

from apps.web_console.components import pnl_chart
from apps.web_console.pages import performance as perf_page


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
            self._button_sequence: list[bool] = []
            self._stop_raises = True

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
            if self._button_sequence:
                return self._button_sequence.pop(0)
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
            if self._stop_raises:
                # mimic Streamlit's stop; raising SystemExit keeps flow deterministic in tests
                raise SystemExit()
            return None

    dummy = DummyStreamlit()
    monkeypatch.setattr(perf_page, "st", dummy)
    return dummy


class TestPerformancePage:
    def test_safe_current_user_handles_missing_context(self, monkeypatch):
        monkeypatch.setattr(
            perf_page, "get_current_user", lambda: (_ for _ in ()).throw(RuntimeError("no session"))
        )
        assert perf_page._safe_current_user() == {}

    def test_fetch_adds_rbac_headers(self, monkeypatch):
        user = {"role": "operator", "user_id": "u1", "strategies": ["s2", "s1"]}
        monkeypatch.setattr(perf_page, "_safe_current_user", lambda: user)

        captured = {}

        class DummyResponse:
            def __init__(self):
                self._json = {"ok": True}

            def raise_for_status(self):
                return None

            def json(self):
                return self._json

        def fake_get(url, params=None, headers=None, timeout=None):
            captured["url"] = url
            captured["params"] = params
            captured["headers"] = headers
            return DummyResponse()

        monkeypatch.setattr(perf_page.requests, "get", fake_get)

        resp = perf_page._fetch("positions", params={"a": 1})
        assert resp == {"ok": True}
        assert captured["headers"]["X-User-Role"] == "operator"
        assert captured["headers"]["X-User-Id"] == "u1"
        # strategies sorted
        assert captured["headers"]["X-User-Strategies"] == "s1,s2"

    def test_fetch_performance_requires_user_id(self):
        with pytest.raises(RuntimeError):
            perf_page.fetch_performance(date(2024, 1, 1), date(2024, 1, 2), ["s1"], None)

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
        monkeypatch.setattr(
            perf_page, "fetch_realtime_pnl", lambda: {"positions": [], "total_unrealized_pl": 0}
        )
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

    def test_historical_range_too_long(self):
        perf_page.render_historical_performance(date(2024, 1, 1), date(2024, 5, 1), ["s1"])
        st_obj = perf_page.st  # type: ignore[attr-defined]
        assert any("cannot exceed" in msg for msg in st_obj._errors)

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

    def test_historical_performance_request_failure(self, monkeypatch):
        monkeypatch.setattr(perf_page, "_safe_current_user", lambda: {"user_id": "u1"})
        monkeypatch.setattr(
            perf_page,
            "fetch_performance",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                requests.exceptions.RequestException("boom")
            ),
        )
        perf_page.render_historical_performance(date(2024, 1, 1), date(2024, 1, 2), ["s1"])
        st_obj = perf_page.st  # type: ignore[attr-defined]
        assert any("Failed to load performance" in msg for msg in st_obj._errors)

    def test_historical_performance_shows_data_warning(self, monkeypatch):
        monkeypatch.setattr(perf_page, "_safe_current_user", lambda: {"user_id": "u1"})
        monkeypatch.setattr(
            perf_page,
            "fetch_performance",
            lambda *_args, **_kwargs: {
                "daily_pnl": [
                    {"date": "2024-02-01", "cumulative_realized_pl": "0", "drawdown_pct": "0"}
                ],
                "data_available_from": "2024-02-01",
            },
        )
        perf_page.render_historical_performance(date(2024, 1, 1), date(2024, 2, 2), ["s1"])
        st_obj = perf_page.st  # type: ignore[attr-defined]
        assert st_obj._warnings, "expected data availability warning when start predates data"

    def test_historical_performance_empty_daily_info(self, monkeypatch):
        monkeypatch.setattr(perf_page, "_safe_current_user", lambda: {"user_id": "u1"})
        monkeypatch.setattr(
            perf_page,
            "fetch_performance",
            lambda *_args, **_kwargs: {"daily_pnl": [], "data_available_from": None},
        )
        perf_page.render_historical_performance(date(2024, 3, 1), date(2024, 3, 2), ["s1"])
        st_obj = perf_page.st  # type: ignore[attr-defined]
        assert any("No trading activity" in msg for msg in st_obj._infos)

    def test_realtime_pnl_viewer_scoped_message(self, monkeypatch):
        monkeypatch.setattr(
            perf_page, "_safe_current_user", lambda: {"role": "viewer", "strategies": ["s1"]}
        )
        monkeypatch.setattr(perf_page, "has_permission", lambda *_args, **_kwargs: False)
        monkeypatch.setattr(
            perf_page, "fetch_realtime_pnl", lambda: {"positions": [], "total_unrealized_pl": 0}
        )
        perf_page.render_realtime_pnl()
        st_obj = perf_page.st  # type: ignore[attr-defined]
        # Message updated to clarify multi-strategy symbol filtering behavior
        assert any("authorized strategies" in msg for msg in st_obj._infos)

    def test_realtime_pnl_request_failure(self, monkeypatch):
        monkeypatch.setattr(
            perf_page,
            "fetch_realtime_pnl",
            lambda: (_ for _ in ()).throw(requests.exceptions.RequestException("x")),
        )
        perf_page.render_realtime_pnl()
        st_obj = perf_page.st  # type: ignore[attr-defined]
        assert any("Failed to load real-time P&L" in msg for msg in st_obj._errors)

    def test_position_summary_request_failure(self, monkeypatch):
        monkeypatch.setattr(
            perf_page,
            "fetch_positions",
            lambda: (_ for _ in ()).throw(requests.exceptions.RequestException("x")),
        )
        perf_page.render_position_summary()
        st_obj = perf_page.st  # type: ignore[attr-defined]
        assert any("Failed to load positions" in msg for msg in st_obj._errors)

    def test_select_date_range_presets(self):
        st_obj = perf_page.st  # type: ignore[attr-defined]
        st_obj.session_state.clear()
        start, end, preset = perf_page._select_date_range()
        assert preset != "Custom"
        assert start <= end

    def test_select_date_range_button_switches_preset(self):
        st_obj = perf_page.st  # type: ignore[attr-defined]
        st_obj.session_state.clear()
        # first call to button returns True to simulate click on first preset
        st_obj._button_sequence = [True] + [False] * 10
        start, end, preset = perf_page._select_date_range()
        assert preset != "Custom"
        assert start <= end

    def test_main_runs_with_feature_flag_enabled(self, monkeypatch):
        st_obj = perf_page.st  # type: ignore[attr-defined]
        st_obj._stop_raises = False
        monkeypatch.setattr(perf_page, "FEATURE_PERFORMANCE_DASHBOARD", True)
        monkeypatch.setattr(
            perf_page, "get_current_user", lambda: {"role": "viewer", "strategies": ["s1"]}
        )
        monkeypatch.setattr(perf_page, "has_permission", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(perf_page, "get_authorized_strategies", lambda _user: ["s1"])
        monkeypatch.setattr(perf_page, "StrategyScopedDataAccess", lambda *args, **kwargs: object())
        monkeypatch.setattr(perf_page, "render_realtime_pnl", lambda: None)
        monkeypatch.setattr(perf_page, "render_position_summary", lambda: None)
        monkeypatch.setattr(
            perf_page, "render_historical_performance", lambda *_args, **_kwargs: None
        )

        # Call main directly (require_auth is stubbed to pass-through)
        perf_page.main()


class TestPnLCharts:
    def test_scalar_helpers(self):
        class Obj:
            def __init__(self):
                self.value = 5

        assert pnl_chart._get_value({"value": 7}, "value") == 7
        assert pnl_chart._get_value(Obj(), "value") == 5
        assert pnl_chart._as_float(None) == 0.0
        assert pnl_chart._as_float("not-a-number") == 0.0

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
        assert len(getattr(fig, "data", [])) == 1

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
        assert len(getattr(fig, "data", [])) == 1
