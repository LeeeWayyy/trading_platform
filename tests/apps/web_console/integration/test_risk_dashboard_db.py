"""Integration-style tests for risk dashboard DB wiring (T6.4a).

These tests verify that the risk dashboard now uses the shared db_pool/redis
utilities and that the AsyncConnectionAdapter remains compatible with the
run_async pattern (fresh event loop per call).
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import types
from contextlib import contextmanager
from typing import Any

import streamlit as st

# Stub jwt early to avoid cryptography import side effects
jwt_stub = types.SimpleNamespace(
    api_jwk=types.SimpleNamespace(),
    algorithms=types.SimpleNamespace(),
    utils=types.SimpleNamespace(),
)
sys.modules.setdefault("jwt", jwt_stub)
sys.modules.setdefault("jwt.api_jwk", jwt_stub.api_jwk)
sys.modules.setdefault("jwt.algorithms", jwt_stub.algorithms)
sys.modules.setdefault("jwt.utils", jwt_stub.utils)

from apps.web_console.services.risk_service import RiskDashboardData


def _passthrough_cache_data(**_kwargs: Any):
    """Return a decorator that leaves the function unchanged (no caching)."""

    def decorator(fn):
        fn.clear = lambda: None  # type: ignore[attr-defined]
        return fn

    return decorator


@contextmanager
def _noop_spinner(*_args: Any, **_kwargs: Any):
    yield


class DummyAdapter:
    """Connection adapter that records the event loop used for each connection."""

    def __init__(self) -> None:
        self.loop_ids: list[int] = []
        self.loops: list[Any] = []

    async def __aenter__(self) -> DummyAdapter:
        return self

    async def __aexit__(self, *_args: Any) -> None:
        return None

    async def _connect(self) -> str:
        loop_id = id(asyncio.get_running_loop())
        self.loop_ids.append(loop_id)
        self.loops.append(asyncio.get_running_loop())
        return f"conn-{len(self.loop_ids)}"

    async def _connection_cm(self):
        conn = await self._connect()

        class _Conn:
            async def __aenter__(self_nonlocal):
                return conn

            async def __aexit__(self_nonlocal, *_args: Any):
                return None

        return _Conn()

    def connection(self):
        return self._connection_cm()


class FakeScopedAccess:
    """Minimal scoped access object that uses the provided db_pool."""

    def __init__(self, db_pool: DummyAdapter, redis_client: Any, user: dict[str, Any]):
        self.db_pool = db_pool
        self.redis_client = redis_client
        self.user = user
        self.user_id = user.get("user_id")
        self.authorized_strategies = user.get("strategies", [])


class FakeRiskService:
    """RiskService stub that opens a DB connection for each call."""

    def __init__(self, scoped_access: FakeScopedAccess):
        self._scoped_access = scoped_access

    async def get_risk_dashboard_data(self) -> RiskDashboardData:
        # Simulate DB use and ensure adapter works with current loop
        async with await self._scoped_access.db_pool.connection():
            pass

        return RiskDashboardData(
            risk_metrics={"total_risk": 0.1},
            factor_exposures=[],
            stress_tests=[],
            var_history=[],
            is_placeholder=False,
            placeholder_reason="",
        )


def _run_async_fresh_loop(coro, timeout: float | None = None):
    """Mimic run_async by creating a fresh event loop per invocation."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(asyncio.wait_for(coro, timeout=timeout))
    finally:
        loop.close()


def test_fetch_risk_data_uses_fresh_connections(monkeypatch: Any) -> None:
    """_fetch_risk_data should work across multiple run_async calls."""
    # Patch Streamlit caching/spinner before reloading the module
    monkeypatch.setattr(st, "cache_data", _passthrough_cache_data)
    monkeypatch.setattr(st, "spinner", _noop_spinner)

    # Reload risk page so decorator uses patched cache_data
    import apps.web_console.pages.risk as risk_page

    importlib.reload(risk_page)

    adapter = DummyAdapter()
    redis_sentinel = object()

    monkeypatch.setattr(risk_page, "get_db_pool", lambda: adapter)
    monkeypatch.setattr(risk_page, "get_redis_client", lambda: redis_sentinel)
    monkeypatch.setattr(
        risk_page, "safe_current_user", lambda: {"user_id": "u1", "strategies": ["s1"]}
    )
    monkeypatch.setattr(risk_page, "run_async", _run_async_fresh_loop)
    monkeypatch.setattr(risk_page, "StrategyScopedDataAccess", FakeScopedAccess)
    monkeypatch.setattr(risk_page, "RiskService", FakeRiskService)

    # First call
    result1 = risk_page._fetch_risk_data(user_id="u1", strategies=("s1",))
    # Second call with different args to avoid cache key collision
    result2 = risk_page._fetch_risk_data(user_id="u1", strategies=("s1", "s2"))

    # Two connections should be opened (fresh per call)
    assert len(adapter.loop_ids) == 2

    # Results should propagate through to the caller
    assert result1["risk_metrics"]["total_risk"] == 0.1
    assert result2["risk_metrics"]["total_risk"] == 0.1
