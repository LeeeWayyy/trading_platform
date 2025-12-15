from __future__ import annotations

import asyncio
import sys
import types
from datetime import date
from typing import Any

import pytest


class DummyStreamlit(types.SimpleNamespace):
    """Lightweight Streamlit stub for page integration test."""

    def __init__(self) -> None:
        super().__init__()
        self.session_state: dict[str, Any] = {}
        self._line_chart_called = False

    def set_page_config(self, *args, **kwargs):
        return None

    def cache_resource(self, func=None, **_kwargs):
        if func is not None:
            return func

        def decorator(fn):
            return fn

        return decorator

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def title(self, *_args, **_kwargs):
        return None

    def caption(self, *_args, **_kwargs):
        return None

    def info(self, *_args, **_kwargs):
        return None

    def warning(self, *_args, **_kwargs):
        return None

    def error(self, *_args, **_kwargs):
        return None

    def subheader(self, *_args, **_kwargs):
        return None

    def stop(self):
        raise RuntimeError("st.stop called")

    def multiselect(self, *_args, **_kwargs):
        return ["s1", "s2"]

    def date_input(self, *_args, **_kwargs):
        return (date(2025, 1, 1), date(2025, 1, 2))

    def spinner(self, *_args, **_kwargs):
        class _Spinner:
            def __enter__(self_inner):
                return None

            def __exit__(self_inner, exc_type, exc, tb):
                return False

        return _Spinner()

    def columns(self, spec):
        count = len(spec) if isinstance(spec, list | tuple) else spec
        return [self for _ in range(count)]

    def slider(self, *_args, **_kwargs):
        return 0.5

    def plotly_chart(self, *_args, **_kwargs):
        return None

    def line_chart(self, *_args, **_kwargs):
        self._line_chart_called = True
        return None

    def radio(self, *_args, **_kwargs):
        return None

    def stop_exception(self):
        return RuntimeError


@pytest.fixture(autouse=True)
def stub_streamlit(monkeypatch):
    """Inject streamlit stub before importing compare page."""
    st_stub = DummyStreamlit()
    sys.modules["streamlit"] = st_stub
    return st_stub


def test_compare_page_renders(monkeypatch, stub_streamlit: DummyStreamlit) -> None:
    # Stub jwt to avoid cryptography bindings
    jwt_stub = types.SimpleNamespace(
        api_jwk=types.SimpleNamespace(),
        algorithms=types.SimpleNamespace(),
        utils=types.SimpleNamespace(),
    )
    sys.modules.setdefault("jwt", jwt_stub)
    sys.modules.setdefault("jwt.api_jwk", jwt_stub.api_jwk)
    sys.modules.setdefault("jwt.algorithms", jwt_stub.algorithms)
    sys.modules.setdefault("jwt.utils", jwt_stub.utils)

    # Stub auth modules to avoid crypto dependencies
    permissions_stub = types.SimpleNamespace(
        Permission=types.SimpleNamespace(VIEW_PNL="view_pnl"),
        has_permission=lambda user, perm: True,
        get_authorized_strategies=lambda user: ["s1", "s2", "s3"],
    )
    session_stub = types.SimpleNamespace(
        get_current_user=lambda: {"user_id": "u1", "strategies": ["s1", "s2", "s3"]},
        require_auth=lambda fn: fn,
    )
    sys.modules["apps.web_console.auth.permissions"] = permissions_stub
    sys.modules["apps.web_console.auth.session_manager"] = session_stub

    # Stub component package to avoid importing bulk_operations
    components_pkg = types.ModuleType("apps.web_console.components")
    components_pkg.__path__ = []
    comparison_charts_stub = types.SimpleNamespace(
        render_equity_comparison=lambda *args, **kwargs: None,
        render_metrics_table=lambda *args, **kwargs: None,
        render_portfolio_simulator=lambda strategies, default: {sid: 0.5 for sid in strategies[:2]},
    )
    corr_stub = types.SimpleNamespace(render_correlation_heatmap=lambda *args, **kwargs: None)
    sys.modules["apps.web_console.components"] = components_pkg
    sys.modules["apps.web_console.components.comparison_charts"] = comparison_charts_stub
    sys.modules["apps.web_console.components.correlation_matrix"] = corr_stub

    # Stub data access and service
    class DummyScopedAccess:
        def __init__(self, db_pool=None, redis_client=None, user=None):
            self.db_pool = db_pool
            self.redis_client = redis_client
            self.user = user
            # ComparisonService uses authorized_strategies to calculate limits
            self.authorized_strategies = ["s1", "s2", "s3"]

    class DummyService:
        def __init__(self, scoped_access):
            self._scoped_access = scoped_access

        async def get_comparison_data(self, *args, **kwargs):
            import pandas as pd

            # Create a minimal pnl_frame DataFrame to avoid st.stop()
            pnl_frame = pd.DataFrame(
                {
                    "date": [date(2025, 1, 1), date(2025, 1, 2)],
                    "s1": [0.01, 0.02],
                    "s2": [0.015, 0.025],
                }
            )
            return {
                "metrics": {"s1": {"total_return": 1}, "s2": {"total_return": 2}},
                "equity_curves": [
                    {"strategy_id": "s1", "equity": [{"date": date(2025, 1, 1), "equity": 1.0}]},
                    {"strategy_id": "s2", "equity": [{"date": date(2025, 1, 1), "equity": 2.0}]},
                ],
                "correlation_matrix": None,
                "default_weights": {"s1": 0.5, "s2": 0.5},
                "pnl_frame": pnl_frame,
                "combined_portfolio": {
                    "equity_curve": [],
                    "total_return": 0.0,
                    "max_drawdown": 0.0,
                },
            }

        def compute_combined_portfolio(self, *_args, **_kwargs):
            return {"equity_curve": [{"date": date(2025, 1, 1), "equity": 1.5}]}

    DummyService.validate_weights = staticmethod(lambda weights: (True, ""))  # type: ignore[attr-defined]

    import importlib

    compare_page = importlib.import_module("apps.web_console.pages.compare")
    monkeypatch.setattr(compare_page, "FEATURE_STRATEGY_COMPARISON", True)
    monkeypatch.setattr(compare_page, "StrategyScopedDataAccess", DummyScopedAccess)
    monkeypatch.setattr(compare_page, "ComparisonService", DummyService)
    # Return a mock db_pool to prevent st.stop() at config guard (line 86-91)
    mock_db_pool = types.SimpleNamespace()
    monkeypatch.setattr(compare_page, "get_db_pool", lambda: mock_db_pool)
    monkeypatch.setattr(compare_page, "get_redis_client", lambda: None)

    def run_async_stub(coro, timeout=None):
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        return loop.run_until_complete(coro)

    monkeypatch.setattr(compare_page, "run_async", run_async_stub)

    # Should run without raising and render combined chart
    compare_page.main()
    assert stub_streamlit._line_chart_called
