"""Tests for Risk Dashboard UI components.

Tests cover:
- Factor exposure chart rendering
- VaR metrics and gauge chart
- Stress test results table and waterfall
- Risk page integration
"""

from __future__ import annotations

import sys
from datetime import date
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

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
auth_permissions_stub.Role = type(
    "Role",
    (),
    {"ADMIN": "admin", "OPERATOR": "operator", "VIEWER": "viewer"},
)


def _has_permission(user, permission):
    return True


def _get_authorized_strategies(user):
    return user.get("strategies", []) if isinstance(user, dict) else []


auth_permissions_stub.has_permission = _has_permission
auth_permissions_stub.get_authorized_strategies = _get_authorized_strategies

session_mgr_stub = type(sys)("apps.web_console.auth.session_manager")
session_mgr_stub.get_current_user = lambda: {"role": "viewer", "user_id": "u1", "strategies": ["s1"]}
session_mgr_stub.get_session_cookie = lambda: None


def _require_auth(func):
    return func


session_mgr_stub.require_auth = _require_auth

sys.modules.setdefault("apps.web_console.auth.permissions", auth_permissions_stub)
sys.modules.setdefault("apps.web_console.auth.session_manager", session_mgr_stub)

# Stub audit_log to avoid full auth stack
audit_log_stub = type(sys)("apps.web_console.auth.audit_log")
audit_log_stub.AuditLogger = MagicMock
sys.modules.setdefault("apps.web_console.auth.audit_log", audit_log_stub)

# Stub user_management service to avoid import chain
user_mgmt_stub = type(sys)("apps.web_console.services.user_management")
user_mgmt_stub.UserManagementService = MagicMock
user_mgmt_stub.list_users = MagicMock
user_mgmt_stub.update_user_role = MagicMock
user_mgmt_stub.update_user_strategies = MagicMock
sys.modules.setdefault("apps.web_console.services.user_management", user_mgmt_stub)

# Stub services module
services_stub = type(sys)("apps.web_console.services")
services_stub.user_management = user_mgmt_stub
sys.modules.setdefault("apps.web_console.services", services_stub)

# Stub bulk_operations to avoid deep import chain
bulk_ops_stub = type(sys)("apps.web_console.components.bulk_operations")
bulk_ops_stub.render_bulk_role_change = MagicMock
bulk_ops_stub.render_bulk_strategy_operations = MagicMock
sys.modules.setdefault("apps.web_console.components.bulk_operations", bulk_ops_stub)

# Stub strategy_assignment to avoid deep import chain
strategy_assign_stub = type(sys)("apps.web_console.components.strategy_assignment")
strategy_assign_stub.render_strategy_assignment = MagicMock
sys.modules.setdefault("apps.web_console.components.strategy_assignment", strategy_assign_stub)

# Stub user_role_editor to avoid deep import chain
role_editor_stub = type(sys)("apps.web_console.components.user_role_editor")
role_editor_stub.render_role_editor = MagicMock
sys.modules.setdefault("apps.web_console.components.user_role_editor", role_editor_stub)

# Stub session_status to avoid deep import chain
session_status_stub = type(sys)("apps.web_console.components.session_status")
session_status_stub.render_session_status = MagicMock
sys.modules.setdefault("apps.web_console.components.session_status", session_status_stub)

# Stub the full auth module to avoid importing the real __init__.py
auth_init_stub = type(sys)("apps.web_console.auth")
auth_init_stub.Permission = auth_permissions_stub.Permission
auth_init_stub.Role = auth_permissions_stub.Role
auth_init_stub.has_permission = _has_permission
auth_init_stub.get_authorized_strategies = _get_authorized_strategies
auth_init_stub.get_current_user = session_mgr_stub.get_current_user
auth_init_stub.require_auth = _require_auth
auth_init_stub.get_session_cookie = session_mgr_stub.get_session_cookie
auth_init_stub.AuditLogger = MagicMock
sys.modules.setdefault("apps.web_console.auth", auth_init_stub)

# Stub strategy_scoped_queries to avoid cryptography dependency
strategy_stub = type(sys)("apps.web_console.data.strategy_scoped_queries")
strategy_stub.StrategyScopedDataAccess = object
sys.modules.setdefault("apps.web_console.data.strategy_scoped_queries", strategy_stub)

# Stub libs.risk.factor_covariance for CANONICAL_FACTOR_ORDER
factor_cov_stub = type(sys)("libs.risk.factor_covariance")
factor_cov_stub.CANONICAL_FACTOR_ORDER = [
    "log_market_cap",
    "book_to_market",
    "momentum_12_1",
    "realized_vol",
    "roe",
    "asset_growth",
]
sys.modules.setdefault("libs.risk.factor_covariance", factor_cov_stub)

# Now import chart components directly (avoid __init__.py which pulls full auth)
from apps.web_console.components.factor_exposure_chart import (
    FACTOR_DISPLAY_NAMES,
    render_factor_exposure,
)
from apps.web_console.components.stress_test_results import (
    SCENARIO_DISPLAY_ORDER,
    SCENARIO_INFO,
    render_factor_waterfall,
    render_scenario_table,
    render_stress_tests,
)
from apps.web_console.components.var_chart import (
    DEFAULT_VAR_LIMIT,
    DEFAULT_WARNING_THRESHOLD,
    render_var_gauge,
    render_var_history,
    render_var_metrics,
)


@pytest.fixture
def sample_factor_exposures():
    """Sample factor exposures for testing."""
    return [
        {"factor_name": "log_market_cap", "exposure": 0.5},
        {"factor_name": "book_to_market", "exposure": -0.3},
        {"factor_name": "momentum_12_1", "exposure": 0.2},
        {"factor_name": "realized_vol", "exposure": 0.1},
        {"factor_name": "roe", "exposure": -0.1},
        {"factor_name": "asset_growth", "exposure": 0.05},
    ]


@pytest.fixture
def sample_risk_metrics():
    """Sample risk metrics for testing."""
    return {
        "total_risk": 0.15,
        "factor_risk": 0.12,
        "specific_risk": 0.08,
        "var_95": 0.025,
        "var_99": 0.035,
        "cvar_95": 0.04,
    }


@pytest.fixture
def sample_stress_tests():
    """Sample stress test results for testing."""
    return [
        {
            "scenario_name": "GFC_2008",
            "scenario_type": "historical",
            "portfolio_pnl": -0.182,
            "factor_impacts": {
                "book_to_market": -0.08,
                "realized_vol": -0.05,
            },
        },
        {
            "scenario_name": "COVID_2020",
            "scenario_type": "historical",
            "portfolio_pnl": -0.145,
            "factor_impacts": {
                "momentum_12_1": -0.06,
            },
        },
        {
            "scenario_name": "RATE_SHOCK",
            "scenario_type": "hypothetical",
            "portfolio_pnl": -0.125,
            "factor_impacts": {
                "book_to_market": 0.03,
                "momentum_12_1": -0.08,
            },
        },
    ]


@pytest.fixture
def sample_var_history():
    """Sample VaR history for testing."""
    return [
        {"date": "2024-01-01", "var_95": 0.02, "daily_pnl": 100},
        {"date": "2024-01-02", "var_95": 0.025, "daily_pnl": -50},
        {"date": "2024-01-03", "var_95": 0.018, "daily_pnl": 75},
    ]


class DummyColumnConfig:
    """Dummy column_config for st.column_config.TextColumn etc."""

    def TextColumn(self, *args, **kwargs):
        return {"type": "text", "args": args, "kwargs": kwargs}

    def NumberColumn(self, *args, **kwargs):
        return {"type": "number", "args": args, "kwargs": kwargs}


class DummyStreamlit:
    """Dummy Streamlit module for testing."""

    def __init__(self):
        self._metrics = []
        self._infos: list[str] = []
        self._warnings: list[str] = []
        self._errors: list[str] = []
        self._captions: list[str] = []
        self._plotly_charts: list[Any] = []
        self._dataframes: list[Any] = []
        self._subheaders: list[str] = []
        self.session_state: dict[str, Any] = {}
        self.column_config = DummyColumnConfig()

    def __enter__(self):
        """Support context manager protocol for with cols[0]: pattern."""
        return self

    def __exit__(self, *args):
        """Support context manager protocol for with cols[0]: pattern."""
        return None

    def subheader(self, text, *_args, **_kwargs):
        self._subheaders.append(text)
        return None

    def error(self, text, *_args, **_kwargs):
        self._errors.append(str(text))
        return None

    def info(self, text, *_args, **_kwargs):
        self._infos.append(str(text))
        return None

    def warning(self, text, *_args, **_kwargs):
        self._warnings.append(str(text))
        return None

    def caption(self, text, *_args, **_kwargs):
        self._captions.append(str(text))
        return None

    def metric(self, label, value, delta=None, help=None, **_kwargs):
        self._metrics.append((label, value, delta))
        return None

    def columns(self, n):
        return [self for _ in range(n)]

    def plotly_chart(self, fig, *_args, **_kwargs):
        self._plotly_charts.append(fig)
        return None

    def dataframe(self, data, *_args, **_kwargs):
        self._dataframes.append(data)
        return None

    def divider(self):
        return None


class TestFactorExposureChart:
    """Tests for factor exposure chart component."""

    def test_factor_display_names_defined(self):
        """Test that factor display names are defined."""
        assert len(FACTOR_DISPLAY_NAMES) > 0
        assert "log_market_cap" in FACTOR_DISPLAY_NAMES
        assert FACTOR_DISPLAY_NAMES["log_market_cap"] == "Size (Market Cap)"

    def test_render_factor_exposure_empty(self, monkeypatch):
        """Test rendering with empty exposures."""
        dummy_st = DummyStreamlit()

        with patch(
            "apps.web_console.components.factor_exposure_chart.st", dummy_st
        ):
            render_factor_exposure([])

        assert any("No factor" in msg for msg in dummy_st._infos)

    def test_render_factor_exposure_valid(self, monkeypatch, sample_factor_exposures):
        """Test rendering with valid exposures creates chart."""
        dummy_st = DummyStreamlit()

        with patch(
            "apps.web_console.components.factor_exposure_chart.st", dummy_st
        ):
            render_factor_exposure(sample_factor_exposures)

        # Should create a plotly chart
        assert len(dummy_st._plotly_charts) == 1

    def test_render_factor_exposure_handles_missing_factor_name(self, monkeypatch):
        """Test handling of missing factor names - filtered by validator."""
        dummy_st = DummyStreamlit()
        exposures = [{"exposure": 0.5}]  # missing factor_name

        with patch(
            "apps.web_console.components.factor_exposure_chart.st", dummy_st
        ):
            render_factor_exposure(exposures)

        # Validator filters invalid entries; empty result shows info message
        assert any("No factor" in msg for msg in dummy_st._infos)


class TestVarChart:
    """Tests for VaR chart components."""

    def test_default_constants(self):
        """Test default VaR constants."""
        assert DEFAULT_VAR_LIMIT == 0.05
        assert DEFAULT_WARNING_THRESHOLD == 0.8

    def test_render_var_metrics_empty(self):
        """Test rendering with empty metrics returns early with info message."""
        dummy_st = DummyStreamlit()

        with patch("apps.web_console.components.var_chart.st", dummy_st):
            render_var_metrics({})

        # Should show info message for invalid data (validation fails)
        assert any("not available" in msg for msg in dummy_st._infos)

    def test_render_var_metrics_valid(self, sample_risk_metrics):
        """Test rendering with valid metrics."""
        dummy_st = DummyStreamlit()

        with patch("apps.web_console.components.var_chart.st", dummy_st):
            render_var_metrics(sample_risk_metrics)

        # Should render metrics
        assert len(dummy_st._metrics) >= 3
        labels = [m[0] for m in dummy_st._metrics]
        assert any("VaR" in label for label in labels)

    def test_render_var_metrics_shows_gauge_at_threshold(self, sample_risk_metrics):
        """Test gauge renders with warning color when near limit."""
        dummy_st = DummyStreamlit()
        # VaR at 4.5% with 5% limit = 90% utilization (above 80% warning)
        metrics = {"total_risk": 0.15, "var_95": 0.045, "var_99": 0.055, "cvar_95": 0.06}

        with patch("apps.web_console.components.var_chart.st", dummy_st):
            render_var_metrics(metrics, var_limit=0.05, warning_threshold=0.8)

        # Should render metrics and gauge
        assert len(dummy_st._metrics) >= 3
        assert len(dummy_st._plotly_charts) >= 1  # Gauge chart

    def test_render_var_gauge_valid(self, sample_risk_metrics):
        """Test VaR gauge rendering."""
        dummy_st = DummyStreamlit()

        with patch("apps.web_console.components.var_chart.st", dummy_st):
            render_var_gauge(
                sample_risk_metrics.get("var_95", 0),
                var_limit=0.05,
            )

        # Should create a plotly chart (gauge)
        assert len(dummy_st._plotly_charts) == 1

    def test_render_var_history_empty(self):
        """Test rendering with empty history."""
        dummy_st = DummyStreamlit()

        with patch("apps.web_console.components.var_chart.st", dummy_st):
            render_var_history([])

        assert any("No VaR" in msg for msg in dummy_st._infos)

    def test_render_var_history_valid(self, sample_var_history):
        """Test rendering with valid history."""
        dummy_st = DummyStreamlit()

        with patch("apps.web_console.components.var_chart.st", dummy_st):
            render_var_history(sample_var_history)

        # Should create a plotly chart
        assert len(dummy_st._plotly_charts) == 1


class TestStressTestResults:
    """Tests for stress test results component."""

    def test_scenario_display_order_defined(self):
        """Test scenario display order is defined."""
        assert len(SCENARIO_DISPLAY_ORDER) > 0
        assert "GFC_2008" in SCENARIO_DISPLAY_ORDER

    def test_scenario_info_defined(self):
        """Test scenario info is defined."""
        assert len(SCENARIO_INFO) > 0
        assert "GFC_2008" in SCENARIO_INFO
        assert "description" in SCENARIO_INFO["GFC_2008"]

    def test_render_stress_tests_empty(self):
        """Test rendering with empty results."""
        dummy_st = DummyStreamlit()

        with patch("apps.web_console.components.stress_test_results.st", dummy_st):
            render_stress_tests([])

        assert any("No stress" in msg for msg in dummy_st._infos)

    def test_render_stress_tests_valid(self, sample_stress_tests):
        """Test rendering with valid results."""
        dummy_st = DummyStreamlit()

        with patch("apps.web_console.components.stress_test_results.st", dummy_st):
            render_stress_tests(sample_stress_tests)

        # Should render subheader and charts/tables
        assert len(dummy_st._subheaders) > 0

    def test_render_scenario_table_valid(self, sample_stress_tests):
        """Test scenario table rendering."""
        dummy_st = DummyStreamlit()

        with patch("apps.web_console.components.stress_test_results.st", dummy_st):
            render_scenario_table(sample_stress_tests)

        # Should create a dataframe
        assert len(dummy_st._dataframes) == 1

    def test_render_factor_waterfall_valid(self, sample_stress_tests):
        """Test factor waterfall chart rendering."""
        dummy_st = DummyStreamlit()
        # Select a scenario with factor impacts
        scenario = sample_stress_tests[0]

        with patch("apps.web_console.components.stress_test_results.st", dummy_st):
            render_factor_waterfall(scenario)

        # Should create a plotly chart
        assert len(dummy_st._plotly_charts) == 1

    def test_render_factor_waterfall_no_impacts(self):
        """Test waterfall with no factor impacts."""
        dummy_st = DummyStreamlit()
        scenario = {
            "scenario_name": "TEST",
            "portfolio_pnl": -0.1,
            "factor_impacts": {},
        }

        with patch("apps.web_console.components.stress_test_results.st", dummy_st):
            render_factor_waterfall(scenario)

        # Should show info message
        assert any("No factor" in msg for msg in dummy_st._infos)


class TestAsyncHelpers:
    """Tests for async helper utilities."""

    def test_run_async_simple_coroutine(self):
        """Test run_async with a simple coroutine."""
        from apps.web_console.utils.async_helpers import run_async

        async def simple_coro():
            return 42

        result = run_async(simple_coro())
        assert result == 42

    def test_run_async_with_exception(self):
        """Test run_async propagates exceptions."""
        from apps.web_console.utils.async_helpers import run_async

        async def failing_coro():
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            run_async(failing_coro())

    def test_run_async_timeout(self):
        """Test run_async timeout behavior."""
        import asyncio
        from concurrent.futures import TimeoutError

        from apps.web_console.utils.async_helpers import run_async

        async def slow_coro():
            await asyncio.sleep(10)
            return 42

        with pytest.raises(TimeoutError):
            run_async(slow_coro(), timeout=0.1)


class TestApiClient:
    """Tests for API client utilities."""

    def test_safe_current_user_handles_missing_context(self, monkeypatch):
        """Test safe_current_user handles missing session."""
        from apps.web_console.utils import api_client

        monkeypatch.setattr(
            api_client,
            "get_current_user",
            lambda: (_ for _ in ()).throw(RuntimeError("no session")),
        )
        assert api_client.safe_current_user() == {}

    def test_safe_current_user_returns_user(self, monkeypatch):
        """Test safe_current_user returns user dict."""
        from apps.web_console.utils import api_client

        user = {"user_id": "u1", "role": "admin"}
        monkeypatch.setattr(api_client, "get_current_user", lambda: user)
        assert api_client.safe_current_user() == user

    def test_get_auth_headers_empty_user(self):
        """Test get_auth_headers with empty user."""
        from apps.web_console.utils.api_client import get_auth_headers

        headers = get_auth_headers({})
        assert headers == {}

    def test_get_auth_headers_full_user(self, monkeypatch):
        """Test get_auth_headers with full user context."""
        from apps.web_console.utils import api_client

        user = {"user_id": "u1", "role": "admin", "strategies": ["s1", "s2"]}
        monkeypatch.setattr(
            api_client, "get_authorized_strategies", lambda _: ["s1", "s2"]
        )

        headers = api_client.get_auth_headers(user)
        assert headers["X-User-Id"] == "u1"
        assert headers["X-User-Role"] == "admin"
        assert headers["X-User-Strategies"] == "s1,s2"
