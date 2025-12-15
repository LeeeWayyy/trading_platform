from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

fastapi = pytest.importorskip("fastapi")
HTTPException = pytest.importorskip("fastapi").HTTPException
TestClient = pytest.importorskip("fastapi.testclient").TestClient

# Stub redis + jwt before importing application modules to avoid cryptography/redis binaries
redis_stub = ModuleType("redis")
redis_stub.exceptions = ModuleType("redis.exceptions")
redis_stub.connection = ModuleType("redis.connection")


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


class _RedisClient:
    def __init__(self, *args, **kwargs):
        pass

    def ping(self):
        return True


redis_stub.Redis = _RedisClient
sys.modules.setdefault("redis", redis_stub)
sys.modules.setdefault("redis.exceptions", redis_stub.exceptions)
sys.modules.setdefault("redis.connection", redis_stub.connection)

jwt_stub = ModuleType("jwt")
jwt_stub.api_jwk = SimpleNamespace(PyJWK=None, PyJWKSet=None)
jwt_stub.algorithms = SimpleNamespace(
    get_default_algorithms=lambda: {},
    has_crypto=lambda: False,
    requires_cryptography=False,
)
jwt_stub.utils = SimpleNamespace()
sys.modules.setdefault("jwt", jwt_stub)
sys.modules.setdefault("jwt.api_jwk", jwt_stub.api_jwk)
sys.modules.setdefault("jwt.algorithms", jwt_stub.algorithms)
sys.modules.setdefault("jwt.utils", jwt_stub.utils)

from fastapi import Request

from apps.execution_gateway import main


def _make_user_context_override(user_ctx: dict) -> callable:
    """Create a dependency override with proper Request signature.

    FastAPI inspects function parameters for dependency resolution.
    Using lambda *_, **__: causes FastAPI to treat _ and __ as required
    query parameters, resulting in 422 errors.
    """

    def override(request: Request) -> dict:
        return user_ctx

    return override


class DummyQueryParams:
    def __init__(self, values=None):
        self._values = values or []

    def getlist(self, *_args, **_kwargs):
        return list(self._values)


def _make_request(user=None, strategies=None):
    req = SimpleNamespace()
    req.state = SimpleNamespace(user=user)
    req.query_params = DummyQueryParams(strategies)
    return req


@pytest.fixture()
def test_client():
    return TestClient(main.app)


def test_build_user_context_missing_user_fails_closed():
    req = _make_request(user=None)
    with pytest.raises(HTTPException) as exc:
        main._build_user_context(req)
    assert exc.value.status_code == 401


def test_build_user_context_missing_role_denied():
    req = _make_request(user={"strategies": ["s1"], "user_id": "u1"})
    with pytest.raises(HTTPException) as exc:
        main._build_user_context(req)
    assert exc.value.status_code == 401


def test_build_user_context_extracts_requested_strategies():
    req = _make_request(
        user={"role": "viewer", "strategies": ["s1"], "user_id": "u1"},
        strategies=["s1"],
    )
    ctx = main._build_user_context(req)
    assert ctx["role"] == "viewer"
    assert ctx["requested_strategies"] == ["s1"]


def test_daily_performance_invalid_strategy_subset(monkeypatch, test_client):
    """Requested strategies outside authorized set should 403."""
    req_user = {
        "role": "viewer",
        "strategies": ["s1"],
        "requested_strategies": ["s1", "s2"],
        "user_id": "u1",
        "user": {"role": "viewer", "strategies": ["s1"], "user_id": "u1"},
    }
    main.app.dependency_overrides[main._build_user_context] = _make_user_context_override(req_user)

    with (monkeypatch.context() as m,):
        m.setattr(main, "db_client", main.db_client)
        m.setattr(main, "redis_client", main.redis_client)
        m.setattr(main, "FEATURE_PERFORMANCE_DASHBOARD", True)
        resp = test_client.get(
            "/api/v1/performance/daily",
            headers={"X-User-Role": "viewer", "X-User-Id": "u1"},
            params={"strategies": ["s1", "s2"]},
        )
    assert resp.status_code == 403
