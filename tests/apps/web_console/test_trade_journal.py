"""Tests for Trade Journal page, components, and data access."""

from __future__ import annotations

import asyncio
import os
import sys
import types
from datetime import UTC, date, datetime
from decimal import Decimal
from importlib import import_module
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Import-time stubs to avoid heavy dependencies (jwt/streamlit/auth stacks)
# ---------------------------------------------------------------------------

# Stub jwt early to avoid cryptography dependency during auth imports
jwt_stub = types.SimpleNamespace(
    api_jwk=types.SimpleNamespace(),
    algorithms=types.SimpleNamespace(),
    utils=types.SimpleNamespace(),
)
sys.modules.setdefault("jwt", jwt_stub)
sys.modules.setdefault("jwt.api_jwk", jwt_stub.api_jwk)
sys.modules.setdefault("jwt.algorithms", jwt_stub.algorithms)
sys.modules.setdefault("jwt.utils", jwt_stub.utils)

# Provide lightweight Streamlit module with runtime stubs for import-time hooks
streamlit_mod = types.ModuleType("streamlit")
runtime_mod = types.ModuleType("streamlit.runtime")
scriptrunner_mod = types.ModuleType("streamlit.runtime.scriptrunner")
scriptrunner_mod.get_script_run_ctx = lambda: None
runtime_mod.scriptrunner = scriptrunner_mod
streamlit_mod.runtime = runtime_mod
streamlit_mod.cache_resource = lambda func=None, **_kwargs: func if func else (lambda f: f)
sys.modules.setdefault("streamlit", streamlit_mod)
sys.modules.setdefault("streamlit.runtime", runtime_mod)
sys.modules.setdefault("streamlit.runtime.scriptrunner", scriptrunner_mod)

# Ensure feature flag defaults to enabled for tests; individual tests override
os.environ.setdefault("FEATURE_TRADE_JOURNAL", "true")

# Stub auth modules to avoid pulling real cryptography/redis stacks
permissions_stub = types.SimpleNamespace(
    Permission=type(
        "Permission",
        (),
        {"VIEW_TRADES": "view_trades", "EXPORT_DATA": "export_data"},
    ),
    has_permission=lambda user, perm: True,
    get_authorized_strategies=lambda user: user.get("strategies", []) if isinstance(user, dict) else [],
)
session_stub = types.SimpleNamespace(
    get_current_user=lambda: {"user_id": "u1", "strategies": ["strat_A"]},
    require_auth=lambda fn: fn,
)
audit_stub = types.ModuleType("apps.web_console.auth.audit_log")

class _AuditLogger:
    def __init__(self, *_args, **_kwargs):
        return None

    async def log_export(self, **_kwargs):
        return None


audit_stub.AuditLogger = _AuditLogger

# Stub auth package to avoid executing heavy __init__
auth_pkg = types.ModuleType("apps.web_console.auth")
auth_pkg.__path__ = []
sys.modules.setdefault("apps.web_console.auth", auth_pkg)
sys.modules.setdefault("apps.web_console.auth.permissions", permissions_stub)
sys.modules.setdefault("apps.web_console.auth.session_manager", session_stub)
sys.modules.setdefault("apps.web_console.auth.audit_log", audit_stub)

# Stub components package to bypass heavy __init__ that imports bulk operations
components_pkg = types.ModuleType("apps.web_console.components")
components_pkg.__path__ = [
    str(Path(__file__).resolve().parents[3] / "apps" / "web_console" / "components")
]
sys.modules.setdefault("apps.web_console.components", components_pkg)

trade_stats = import_module("apps.web_console.components.trade_stats")
trade_table = import_module("apps.web_console.components.trade_table")
from apps.web_console.data.strategy_scoped_queries import (
    StrategyScopedDataAccess,
    _date_to_utc_datetime,
)
from apps.web_console.pages import journal as journal_page


class DummySpinner:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return False


class DummyStreamlit:
    """Minimal Streamlit stub capturing rendered output."""

    def __init__(self) -> None:
        self._infos: list[str] = []
        self._warnings: list[str] = []
        self._errors: list[str] = []
        self._captions: list[str] = []
        self._metrics: list[tuple[str, Any, Any]] = []
        self._dataframes: list[Any] = []
        self._download_buttons: list[tuple[Any, ...]] = []
        self._button_sequence: list[bool] = []
        self._selectbox_overrides: dict[str, Any] = {}
        self._text_inputs: dict[str, str] = {}
        self._date_input_value: Any = (date.today(), date.today())
        self._stop_raises = True
        self.session_state: dict[str, Any] = {}
        self.runtime = types.SimpleNamespace(
            scriptrunner=types.SimpleNamespace(get_script_run_ctx=lambda: None)
        )

    # Basic UI helpers -----------------------------------------------------
    def set_page_config(self, *_args, **_kwargs):
        return None

    def title(self, *_args, **_kwargs):
        return None

    def subheader(self, *_args, **_kwargs):
        return None

    def write(self, *_args, **_kwargs):
        return None

    def divider(self):
        return None

    # Messaging ------------------------------------------------------------
    def info(self, msg, *_args, **_kwargs):
        self._infos.append(str(msg))
        return None

    def warning(self, msg, *_args, **_kwargs):
        self._warnings.append(str(msg))
        return None

    def error(self, msg, *_args, **_kwargs):
        self._errors.append(str(msg))
        return None

    def caption(self, msg, *_args, **_kwargs):
        self._captions.append(str(msg))
        return None

    # Widgets --------------------------------------------------------------
    def selectbox(self, label, options, index=0):
        if label in self._selectbox_overrides:
            return self._selectbox_overrides[label]
        if options:
            return options[index] if index < len(options) else options[0]
        return None

    def text_input(self, label, default="", **_kwargs):
        return self._text_inputs.get(label, default)

    def date_input(self, *_args, **_kwargs):
        return self._date_input_value

    def columns(self, spec):
        count = len(spec) if isinstance(spec, list | tuple) else spec
        return [self for _ in range(count)]

    def button(self, *_args, **_kwargs):
        if self._button_sequence:
            return self._button_sequence.pop(0)
        return False

    def metric(self, label, value, delta=None):
        self._metrics.append((label, value, delta))
        return None

    def cache_resource(self, func=None, **_kwargs):
        if func is not None:
            return func

        def decorator(fn):
            return fn

        return decorator

    def dataframe(self, df, **_kwargs):
        self._dataframes.append(df)
        return None

    def spinner(self, *_args, **_kwargs):
        return DummySpinner()

    def download_button(self, *args, **kwargs):
        self._download_buttons.append((args, kwargs))
        return None

    # Control flow ---------------------------------------------------------
    def stop(self):
        if self._stop_raises:
            raise SystemExit()
        return None

    def rerun(self):
        raise SystemExit()


@pytest.fixture(autouse=True)
def mock_streamlit(monkeypatch):
    """Replace Streamlit in target modules with dummy stub per test."""

    dummy = DummyStreamlit()
    sys.modules["streamlit"] = dummy  # future imports
    sys.modules["streamlit.runtime"] = dummy.runtime
    sys.modules["streamlit.runtime.scriptrunner"] = dummy.runtime.scriptrunner
    monkeypatch.setattr(trade_stats, "st", dummy)
    monkeypatch.setattr(trade_table, "st", dummy)
    monkeypatch.setattr(journal_page, "st", dummy)
    return dummy


@pytest.fixture()
def mock_db_pool():
    """Async-friendly db_pool stub compatible with acquire_connection()."""

    conn = AsyncMock()
    cursor = AsyncMock()
    cursor.fetchall.return_value = []
    conn.execute.return_value = cursor

    class Pool:
        def __init__(self, connection):
            self._conn = connection

        def connection(self):
            class _Ctx:
                async def __aenter__(self_inner):
                    return self._conn

                async def __aexit__(self_inner, exc_type, exc, tb):
                    return False

            return _Ctx()

    pool = Pool(conn)
    pool._conn = conn  # expose for assertions
    return pool


@pytest.fixture(autouse=True)
def mock_authorized_strategies(monkeypatch):
    """Ensure tests use user-provided strategies via auth helper.

    StrategyScopedDataAccess now requires get_authorized_strategies to
    return non-empty strategy lists; we mock the permission helper to
    mirror the test user's ``strategies`` field for both the data access
    module and the journal page.
    """

    permissions_mod = import_module("apps.web_console.auth.permissions")
    strategy_scoped_queries = import_module("apps.web_console.data.strategy_scoped_queries")

    def _authorized(user):
        return user.get("strategies", []) if isinstance(user, dict) else []

    # Patch both the permissions helper and the data access module's local import
    # so StrategyScopedDataAccess always uses the test user's strategies.
    monkeypatch.setattr(permissions_mod, "get_authorized_strategies", _authorized, raising=False)
    monkeypatch.setattr(strategy_scoped_queries, "get_authorized_strategies", _authorized, raising=False)
    monkeypatch.setattr(journal_page, "get_authorized_strategies", _authorized, raising=False)
    return _authorized


# ---------------------------------------------------------------------------
# Trade statistics helpers
# ---------------------------------------------------------------------------


class TestTradeStatistics:
    def test_win_rate_zero_trades(self):
        assert trade_stats.calculate_win_rate(0, 0) == 0.0

    def test_win_rate_all_winners(self):
        assert trade_stats.calculate_win_rate(10, 10) == 100.0

    def test_win_rate_mixed(self):
        assert trade_stats.calculate_win_rate(6, 10) == 60.0

    def test_profit_factor_zero_loss(self):
        result = trade_stats.calculate_profit_factor(Decimal("100"), Decimal("0"))
        assert result is None

    def test_profit_factor_normal(self):
        result = trade_stats.calculate_profit_factor(Decimal("200"), Decimal("100"))
        assert result == 2.0


# ---------------------------------------------------------------------------
# Trade table rendering & helpers
# ---------------------------------------------------------------------------


class TestTradeTable:
    def test_render_empty_shows_info(self, mock_streamlit: DummyStreamlit):
        trade_table.render_trade_table([], 50, 0)
        assert any("No trades" in msg for msg in mock_streamlit._infos)

    def test_pnl_coloring_positive(self):
        assert "green" in trade_table._pnl_color(100.0)

    def test_pnl_coloring_negative(self):
        assert "red" in trade_table._pnl_color(-50.0)

    def test_pnl_coloring_zero(self):
        assert trade_table._pnl_color(0.0) == ""

    def test_format_decimal_none(self):
        assert trade_table._format_decimal(None) == 0.0

    def test_format_decimal_from_decimal(self):
        assert trade_table._format_decimal(Decimal("123.45")) == 123.45


# ---------------------------------------------------------------------------
# Date semantics
# ---------------------------------------------------------------------------


class TestDateSemantics:
    def test_date_to_utc_datetime_returns_aware(self):
        result = _date_to_utc_datetime(date(2024, 6, 15))
        assert isinstance(result, datetime)
        assert result == datetime(2024, 6, 15, 0, 0, 0, tzinfo=UTC)
        assert result.tzinfo == UTC

    def test_date_to_utc_datetime_year_boundary(self):
        result = _date_to_utc_datetime(date(2024, 12, 31))
        assert result == datetime(2024, 12, 31, 0, 0, 0, tzinfo=UTC)
        assert result.tzinfo == UTC


# ---------------------------------------------------------------------------
# Data access helpers
# ---------------------------------------------------------------------------


class TestDataAccessExtensions:
    @pytest.mark.asyncio()
    async def test_get_trades_with_date_filter(self, mock_db_pool):
        user = {"user_id": "u1", "strategies": ["strat_A"]}
        data_access = StrategyScopedDataAccess(mock_db_pool, None, user)

        await data_access.get_trades(date_from=date(2024, 6, 1), date_to=date(2024, 6, 30))

        call = mock_db_pool._conn.execute.await_args
        query = call.args[0]
        params = call.args[1]
        assert "executed_at >= %s" in query
        assert "executed_at < %s" in query
        date_params = [p for p in params if isinstance(p, datetime)]
        assert len(date_params) == 2
        assert date_params[0] == datetime(2024, 6, 1, 0, 0, 0, tzinfo=UTC)
        assert date_params[1] == datetime(2024, 6, 30, 0, 0, 0, tzinfo=UTC)
        assert all(p.tzinfo == UTC for p in date_params)

    @pytest.mark.asyncio()
    async def test_get_trade_stats_sql_aggregation(self, mock_db_pool):
        row = {
            "total_trades": 10,
            "winning_trades": 6,
            "losing_trades": 3,
            "break_even_trades": 1,
            "total_realized_pnl": Decimal("500.00"),
            "gross_profit": Decimal("800.00"),
            "gross_loss": Decimal("300.00"),
            "avg_win": Decimal("133.33"),
            "avg_loss": Decimal("-100.00"),
            "largest_win": Decimal("250.00"),
            "largest_loss": Decimal("-150.00"),
        }
        cursor = AsyncMock()
        cursor.fetchall.return_value = [row]
        mock_db_pool._conn.execute.return_value = cursor

        user = {"user_id": "u1", "strategies": ["strat_A"]}
        data_access = StrategyScopedDataAccess(mock_db_pool, None, user)

        result = await data_access.get_trade_stats()

        assert result["total_trades"] == 10
        assert result["winning_trades"] == 6
        assert result["losing_trades"] == 3
        assert result["break_even_trades"] == 1
        assert isinstance(result["total_realized_pnl"], Decimal)
        assert isinstance(result["gross_profit"], Decimal)

    @pytest.mark.asyncio()
    async def test_strategy_scoping_enforced_ignores_user_input(self, mock_db_pool):
        cursor = AsyncMock()
        cursor.fetchall.return_value = []
        mock_db_pool._conn.execute.return_value = cursor

        user = {"user_id": "u1", "strategies": ["strat_A"]}
        data_access = StrategyScopedDataAccess(mock_db_pool, None, user)

        await data_access.get_trades(strategy_id="strat_B")

        call = mock_db_pool._conn.execute.await_args
        query = call.args[0]
        params = call.args[1]
        assert "strategy_id = ANY(%s)" in query
        assert params[0] == ["strat_A"]


# ---------------------------------------------------------------------------
# Journal page gating & permissions
# ---------------------------------------------------------------------------


class TestJournalPage:
    def test_feature_flag_disabled(self, monkeypatch, mock_streamlit: DummyStreamlit):
        monkeypatch.setattr(journal_page, "FEATURE_TRADE_JOURNAL", False)
        journal_page.main()
        assert any("Feature not available" in msg for msg in mock_streamlit._infos)

    def test_permission_denied_without_view_trades(self, monkeypatch, mock_streamlit: DummyStreamlit):
        monkeypatch.setattr(journal_page, "FEATURE_TRADE_JOURNAL", True)
        monkeypatch.setattr(journal_page, "get_current_user", lambda: {"user_id": "u1"})
        monkeypatch.setattr(journal_page, "has_permission", lambda _user, _perm: False)
        with pytest.raises(SystemExit):
            journal_page.main()
        assert any("Permission denied" in msg for msg in mock_streamlit._errors)

    def test_no_strategies_shows_warning(self, monkeypatch, mock_streamlit: DummyStreamlit):
        monkeypatch.setattr(journal_page, "FEATURE_TRADE_JOURNAL", True)
        monkeypatch.setattr(journal_page, "get_current_user", lambda: {"user_id": "u1"})
        monkeypatch.setattr(journal_page, "has_permission", lambda *_args, **_kwargs: True)
        monkeypatch.setattr(journal_page, "get_authorized_strategies", lambda _user: [])
        with pytest.raises(SystemExit):
            journal_page.main()
        assert any("don't have access" in msg.lower() for msg in mock_streamlit._warnings)

    def test_export_requires_permission(self, monkeypatch, mock_streamlit: DummyStreamlit):
        monkeypatch.setattr(journal_page, "has_permission", lambda *_args, **_kwargs: False)
        dummy_data_access = object()
        journal_page._render_export_section(
            dummy_data_access,
            {"user_id": "u1"},
            date(2024, 1, 1),
            date(2024, 1, 2),
            None,
            None,
        )
        assert any("Export permission required" in msg for msg in mock_streamlit._infos)
        assert not mock_streamlit._download_buttons


# ---------------------------------------------------------------------------
# Audit logging on export
# ---------------------------------------------------------------------------


class TestAuditLogging:
    def _run_async_immediate(self, coro):
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(coro)
        finally:
            loop.close()

    def test_export_audit_includes_metadata(self, monkeypatch, mock_streamlit: DummyStreamlit):
        trades = [
            {
                "executed_at": datetime(2024, 1, 1, 0, 0, tzinfo=UTC),
                "symbol": "AAPL",
                "side": "buy",
                "qty": 1,
                "price": Decimal("10"),
                "realized_pnl": Decimal("1.5"),
                "strategy_id": "strat_A",
            }
        ]

        class FakeDataAccess:
            async def stream_trades_for_export(self, **_kwargs):
                for trade in trades:
                    yield trade

        audit_logger = MagicMock()
        audit_logger.log_export = AsyncMock()

        monkeypatch.setattr(journal_page, "AuditLogger", lambda _db_pool: audit_logger)
        monkeypatch.setattr(journal_page, "get_db_pool", lambda: "db_pool_stub")
        monkeypatch.setattr(journal_page, "get_authorized_strategies", lambda _user: ["strat_A"])
        monkeypatch.setattr(journal_page, "run_async", self._run_async_immediate)

        user = {"user_id": "u1", "strategies": ["strat_A"]}
        journal_page._do_export(
            FakeDataAccess(),
            user,
            "csv",
            date(2024, 1, 1),
            date(2024, 1, 2),
            "AAPL",
            "buy",
        )

        assert audit_logger.log_export.await_args is not None
        kwargs = audit_logger.log_export.await_args.kwargs
        assert kwargs["user_id"] == "u1"
        assert kwargs["export_type"] == "csv"
        metadata = kwargs["metadata"]
        assert metadata["date_from"] == date(2024, 1, 1)
        assert metadata["date_to"] == date(2024, 1, 2)
        assert metadata["filters"] == {"symbol": "AAPL", "side": "buy"}
        assert metadata["strategy_ids"] == ["strat_A"]
        assert mock_streamlit._download_buttons, "Download button should be rendered after export"
