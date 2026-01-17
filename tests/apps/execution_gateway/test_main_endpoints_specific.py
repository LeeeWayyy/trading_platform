from __future__ import annotations

from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

# Skip entire module if fastapi isn't available in the test env
fastapi = pytest.importorskip("fastapi")
TestClient = pytest.importorskip("fastapi.testclient").TestClient

# Lightweight stubs to avoid loading real redis/crypto deps before importing app
import sys
from types import ModuleType

redis_stub = ModuleType("redis")
redis_stub.__path__ = []  # Mark as package so redis.asyncio can be imported
redis_stub.exceptions = ModuleType("redis.exceptions")
redis_stub.connection = ModuleType("redis.connection")
redis_stub.asyncio = ModuleType("redis.asyncio")
redis_stub.lock = ModuleType("redis.lock")


class _RedisError(Exception):
    pass


class _ConnectionPool:
    def __init__(self, *args, **kwargs):
        pass

    def disconnect(self):
        pass


redis_stub.exceptions.RedisError = _RedisError
redis_stub.exceptions.ConnectionError = _RedisError  # Alias for tests
redis_stub.exceptions.TimeoutError = _RedisError  # Alias for tests
redis_stub.connection.ConnectionPool = _ConnectionPool
redis_stub.lock.Lock = object


class _RedisClient:
    def __init__(self, *args, **kwargs):
        pass

    def ping(self):
        return True


redis_stub.asyncio.Redis = _RedisClient
redis_stub.Redis = _RedisClient
sys.modules.setdefault("redis", redis_stub)
sys.modules.setdefault("redis.exceptions", redis_stub.exceptions)
sys.modules.setdefault("redis.connection", redis_stub.connection)
sys.modules.setdefault("redis.asyncio", redis_stub.asyncio)
sys.modules.setdefault("redis.lock", redis_stub.lock)

jwt_stub = ModuleType("jwt")
jwt_stub.api_jwk = SimpleNamespace(PyJWK=None, PyJWKSet=None)
jwt_stub.algorithms = SimpleNamespace(
    get_default_algorithms=lambda: {},
    has_crypto=lambda: False,
    requires_cryptography=False,
    ECAlgorithm=object,
    RSAAlgorithm=object,
)
jwt_stub.utils = SimpleNamespace()
sys.modules.setdefault("jwt", jwt_stub)
sys.modules.setdefault("jwt.api_jwk", jwt_stub.api_jwk)
sys.modules.setdefault("jwt.algorithms", jwt_stub.algorithms)
sys.modules.setdefault("jwt.utils", jwt_stub.utils)

# Import Request before main to ensure it's available for the override function.
# The stubs above must be set before importing main which triggers the full import chain.
from starlette.requests import (
    Request,
)

# Use starlette directly to avoid fastapi import side-effects
from apps.execution_gateway import main
from apps.execution_gateway.routes import positions as positions_routes
from apps.execution_gateway.services.auth_helpers import build_user_context


def _make_user_context_override(user_ctx: dict) -> callable:
    """Create a dependency override with proper Request signature.

    FastAPI inspects function parameters for dependency resolution.
    Using lambda *_, **__: causes FastAPI to treat _ and __ as required
    query parameters, resulting in 422 errors. This helper creates a
    properly-typed override function.
    """

    def override(request: Request) -> dict:
        return user_ctx

    return override


@pytest.fixture()
def test_client():
    return TestClient(main.app)


def test_get_daily_performance_happy_path(monkeypatch, test_client):
    # Provide authorized viewer with a single strategy
    user_ctx = {
        "role": "viewer",
        "strategies": ["alpha"],
        "requested_strategies": ["alpha"],
        "user_id": "u1",
        "user": {"role": "viewer", "strategies": ["alpha"], "user_id": "u1"},
    }
    main.app.dependency_overrides[build_user_context] = _make_user_context_override(user_ctx)

    # Mock DB + Redis interactions
    monkeypatch.setattr(positions_routes, "FEATURE_PERFORMANCE_DASHBOARD", True)
    monkeypatch.setattr(main.app.state.context, "redis", None)
    fake_rows = [
        {
            "trade_date": date(2024, 1, 1),
            "daily_realized_pl": Decimal("10"),
            "closing_trade_count": 1,
        }
    ]
    monkeypatch.setattr(
        main.app.state.context.db, "get_daily_pnl_history", lambda *a, **k: fake_rows
    )
    monkeypatch.setattr(
        main.app.state.context.db, "get_data_availability_date", lambda: date(2024, 1, 1)
    )

    resp = test_client.get(
        "/api/v1/performance/daily",
        params={"start_date": "2024-01-01", "end_date": "2024-01-02", "strategies": ["alpha"]},
        headers={"X-User-Role": "viewer", "X-User-Id": "u1"},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["total_realized_pl"] == "10"
    assert body["daily_pnl"][0]["cumulative_realized_pl"] == "10"


def test_get_realtime_pnl_denies_without_strategy_access(monkeypatch, test_client):
    # User with no strategies and no VIEW_ALL_STRATEGIES permission
    user_ctx = {
        "role": "viewer",
        "strategies": [],
        "requested_strategies": [],
        "user_id": "u1",
        "user": {"role": "viewer", "strategies": [], "user_id": "u1"},
    }
    main.app.dependency_overrides[build_user_context] = _make_user_context_override(user_ctx)

    monkeypatch.setattr(
        "apps.execution_gateway.routes.positions.has_permission", lambda *_args, **_kwargs: False
    )

    resp = test_client.get("/api/v1/positions/pnl/realtime")
    assert resp.status_code == 403


def test_get_realtime_pnl_allows_authorized_and_returns_zero(monkeypatch, test_client):
    user_ctx = {
        "role": "viewer",
        "strategies": ["alpha"],
        "requested_strategies": ["alpha"],
        "user_id": "u1",
        "user": {"role": "viewer", "strategies": ["alpha"], "user_id": "u1"},
    }
    main.app.dependency_overrides[build_user_context] = _make_user_context_override(user_ctx)

    # Authorize strategy access, no positions
    monkeypatch.setattr(
        "apps.execution_gateway.routes.positions.has_permission", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(
        main.app.state.context.db,
        "get_positions_for_strategies",
        lambda *_args, **_kwargs: [],
    )

    resp = test_client.get("/api/v1/positions/pnl/realtime")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_positions"] == 0


def test_build_user_context_accepts_obj_with_role(monkeypatch):
    req = SimpleNamespace(
        state=SimpleNamespace(user=SimpleNamespace(role="viewer", strategies=["s1"], id="u1")),
        query_params=SimpleNamespace(getlist=lambda *_args, **_kwargs: []),
    )
    ctx = build_user_context(req)
    assert ctx["role"] == "viewer"
    assert ctx["user_id"] == "u1"
