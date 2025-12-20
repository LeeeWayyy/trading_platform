"""Broader coverage for execution_gateway.main FastAPI app."""

from __future__ import annotations

import sys
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import ModuleType, SimpleNamespace

import pytest
from fastapi.testclient import TestClient

# Stub redis + jwt early to avoid binary deps during import
redis_stub = ModuleType("redis")
redis_stub.exceptions = ModuleType("redis.exceptions")


class _RedisError(Exception):
    pass


redis_stub.exceptions.RedisError = _RedisError


class _RedisClient:
    def __init__(self, *args, **kwargs):
        pass

    def ping(self):
        return True

    def mget(self, keys):
        return [None for _ in keys]

    def get(self, key):
        return None

    def smembers(self, key):
        return []

    def delete(self, *keys):
        return True

    def set(self, key, val, ttl=None):
        return True

    def pipeline(self):
        return self

    def sadd(self, key, *members):
        return 0

    def expire(self, *_args, **_kwargs):
        return True

    def execute(self):
        return True

    def health_check(self):
        return True


redis_stub.Redis = _RedisClient
sys.modules.setdefault("redis", redis_stub)
sys.modules.setdefault("redis.exceptions", redis_stub.exceptions)

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

# Stub alpaca_client before importing main to avoid optional deps
import types

alpaca_stub = types.ModuleType("apps.execution_gateway.alpaca_client")


class _DummyAlpacaErr(Exception): ...


alpaca_stub.AlpacaConnectionError = _DummyAlpacaErr
alpaca_stub.AlpacaExecutor = type(
    "AlpacaExecutor",
    (),
    {
        "__init__": lambda self, *a, **k: None,
        "check_connection": lambda self: True,
        "submit_order": lambda self, order, cid: {"id": "b1", "status": "accepted"},
    },
)
alpaca_stub.AlpacaRejectionError = _DummyAlpacaErr
alpaca_stub.AlpacaValidationError = _DummyAlpacaErr
sys.modules.setdefault("apps.execution_gateway.alpaca_client", alpaca_stub)

# Stub RedisClient to avoid network connection during import
redis_client_stub = types.ModuleType("libs.redis_client")


class _DummyPipeline:
    """Pipeline stub that stores the transaction flag for test compatibility."""

    def __init__(self, transaction=True):
        self.transaction = transaction

    def sadd(self, *a, **k):
        return self

    def expire(self, *a, **k):
        return self

    def execute(self):
        return []


class _DummyRedisClient:
    def __init__(self, *a, **k): ...
    def health_check(self):
        return True

    def mget(self, *_args, **_kwargs):
        return []

    def get(self, *_args, **_kwargs):
        return None

    def set(self, *_args, **_kwargs):
        return True

    def delete(self, *_keys):
        return len(_keys)  # Return count of deleted keys

    def pipeline(self, transaction=True):
        return _DummyPipeline(transaction)

    def sadd(self, *a, **k):
        return 0

    def expire(self, *a, **k):
        return True

    def execute(self):
        return []

    def smembers(self, key):
        return set()

    def sscan_iter(self, key):
        return iter([])


class _DummyRedisKeys:
    CIRCUIT_STATE = "cb:state"
    KILL_STATE = "ks:state"
    PRICE_PREFIX = "price:"


class _DummyConnectionPool:
    """Stub ConnectionPool for test isolation."""

    def __init__(self, *args, **kwargs):
        pass

    def disconnect(self):
        pass


# Stub redis module for libs.redis_client.client.redis access
class _DummyRedisModule:
    """Stub redis module that other tests may try to patch."""

    Redis = _DummyRedisClient
    ConnectionPool = _DummyConnectionPool


redis_client_stub.RedisClient = _DummyRedisClient
redis_client_stub.RedisConnectionError = RuntimeError
redis_client_stub.RedisKeys = _DummyRedisKeys
redis_client_stub.ConnectionPool = _DummyConnectionPool
redis_client_stub.redis = _DummyRedisModule()  # For libs.redis_client.client.redis access
sys.modules.setdefault("libs.redis_client", redis_client_stub)
sys.modules.setdefault("libs.redis_client.client", redis_client_stub)
sys.modules.setdefault("libs.redis_client.keys", redis_client_stub)

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


class DummyDB:
    """Minimal DB client faking responses for multiple endpoints."""

    def __init__(self):
        self.orders = {}
        self.positions = []
        self.daily_rows = []

    def check_connection(self):
        return True

    def get_order_by_client_id(self, cid):
        return self.orders.get(cid)

    def create_order(
        self,
        client_order_id,
        strategy_id,
        order_request,
        status,
        broker_order_id=None,
        error_message=None,
    ):
        od = main.OrderDetail(
            client_order_id=client_order_id,
            strategy_id=strategy_id,
            symbol=order_request.symbol,
            side=order_request.side,
            qty=order_request.qty,
            order_type=order_request.order_type,
            limit_price=order_request.limit_price,
            stop_price=order_request.stop_price,
            time_in_force=order_request.time_in_force,
            status=status,
            broker_order_id=broker_order_id,
            error_message=error_message,
            retry_count=0,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
            filled_qty=Decimal("0"),
        )
        self.orders[client_order_id] = od
        return od

    def get_all_positions(self):
        return self.positions

    def get_positions_for_strategies(self, _strats):
        return self.positions

    def get_position_by_symbol(self, symbol):
        return 0

    def get_daily_pnl_history(self, start, end, strats):
        return self.daily_rows

    def get_data_availability_date(self):
        return None

    def transaction(self):
        class Tx:
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, exc_type, exc, tb):
                return False

        return Tx()

    def get_order_for_update(self, cid, conn):
        return self.orders.get(cid)

    def get_position_for_update(self, symbol, conn):
        return None

    def update_position_on_fill_with_conn(self, symbol, fill_qty, fill_price, side, conn):
        return SimpleNamespace(realized_pl=Decimal("0"))

    def append_fill_to_order_metadata(self, client_order_id, fill_data, conn):
        return None

    def update_order_status_with_conn(
        self,
        client_order_id,
        status,
        filled_qty,
        filled_avg_price,
        filled_at,
        conn,
        broker_order_id=None,
    ):
        return None


class DummyKillSwitch:
    def __init__(self):
        self.engaged = False

    def is_engaged(self):
        return self.engaged

    def engage(self, **_):
        self.engaged = True

    def disengage(self, **_):
        self.engaged = False

    def get_status(self):
        return {"state": "ENGAGED" if self.engaged else "ACTIVE"}


class DummyBreaker:
    def __init__(self, tripped=False):
        self._tripped = tripped

    def is_tripped(self):
        return self._tripped

    def get_trip_reason(self):
        return "test trip"


class DummyReservation:
    def __init__(self):
        self.release_calls = 0
        self.confirm_calls = 0

    def reserve(self, **_):
        return SimpleNamespace(
            success=True, token="t", previous_position=0, new_position=1, reason=None
        )

    def release(self, *_args):
        self.release_calls += 1

    def confirm(self, *_args):
        self.confirm_calls += 1


@pytest.fixture()
def app_client(monkeypatch):
    # httpx>=0.28 removed the 'app' kwarg that Starlette's TestClient still passes.
    # Patch httpx.Client.__init__ to drop the unexpected argument for compatibility.
    import httpx

    original_client_init = httpx.Client.__init__

    def _patched_client_init(self, *args, **kwargs):
        kwargs.pop("app", None)
        return original_client_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "__init__", _patched_client_init)

    fake_db = DummyDB()
    fake_db.positions = [
        main.Position(
            symbol="AAPL",
            qty=Decimal("1"),
            avg_entry_price=Decimal("10"),
            current_price=Decimal("11"),
            unrealized_pl=Decimal("1"),
            realized_pl=Decimal("0"),
            updated_at=datetime.now(UTC),
        )
    ]
    fake_db.daily_rows = [
        {
            "trade_date": date(2024, 1, 1),
            "daily_realized_pl": Decimal("1"),
            "closing_trade_count": 1,
        }
    ]
    monkeypatch.setattr(main, "db_client", fake_db)
    monkeypatch.setattr(main, "redis_client", _RedisClient())
    main.recovery_manager._state.kill_switch = DummyKillSwitch()
    main.recovery_manager._state.circuit_breaker = DummyBreaker(tripped=False)
    main.recovery_manager._state.position_reservation = DummyReservation()
    main.recovery_manager.set_kill_switch_unavailable(False)
    main.recovery_manager.set_circuit_breaker_unavailable(False)
    main.recovery_manager.set_position_reservation_unavailable(False)
    monkeypatch.setattr(main, "FEATURE_PERFORMANCE_DASHBOARD", True)

    # Clear any stale dependency overrides from previous tests
    main.app.dependency_overrides.clear()

    client = TestClient(main.app)
    yield client

    # Cleanup: clear dependency_overrides after each test to prevent pollution
    main.app.dependency_overrides.clear()


def test_root_and_health(app_client):
    resp = app_client.get("/")
    assert resp.status_code == 200
    health = app_client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] in {"healthy", "degraded"}


def test_submit_order_dry_run_path(app_client):
    order = {"symbol": "AAPL", "side": "buy", "qty": 1, "order_type": "market"}
    resp = app_client.post("/api/v1/orders", json=order)
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "dry_run"


def test_submit_order_blocked_by_circuit_breaker(monkeypatch, app_client):
    main.recovery_manager._state.circuit_breaker = DummyBreaker(tripped=True)
    main.recovery_manager.set_circuit_breaker_unavailable(False)
    order = {"symbol": "AAPL", "side": "buy", "qty": 1, "order_type": "market"}
    resp = app_client.post("/api/v1/orders", json=order)
    assert resp.status_code == 503


def test_kill_switch_endpoints(app_client):
    engage = app_client.post(
        "/api/v1/kill-switch/engage", json={"reason": "test", "operator": "op"}
    )
    assert engage.status_code == 200
    status = app_client.get("/api/v1/kill-switch/status")
    assert status.status_code == 200
    disengage = app_client.post("/api/v1/kill-switch/disengage", json={"operator": "op"})
    assert disengage.status_code == 200


def test_realtime_pnl_endpoint(app_client):
    resp = app_client.get("/api/v1/positions/pnl/realtime")
    # With no injected user context, endpoint must fail closed (unauthenticated)
    assert resp.status_code == 401


def test_performance_endpoint_cache_fallback(monkeypatch, app_client):
    # Force redis_client.get to return None to exercise DB path
    class RC(_RedisClient):
        def __init__(self):
            super().__init__()

        def get(self, *_):
            return None

    monkeypatch.setattr(main, "redis_client", RC())
    # Provide user context with strategies
    ctx = {
        "role": "viewer",
        "strategies": ["alpha"],
        "requested_strategies": ["alpha"],
        "user_id": "u1",
        "user": {"role": "viewer", "strategies": ["alpha"], "user_id": "u1"},
    }
    main.app.dependency_overrides[main._build_user_context] = _make_user_context_override(ctx)
    # Provide explicit dates inside allowed 90‑day window to avoid 422
    resp = app_client.get(
        "/api/v1/performance/daily",
        params={
            "strategies": ["alpha"],
            "start_date": (date.today() - timedelta(days=7)).isoformat(),
            "end_date": date.today().isoformat(),
        },
    )
    assert resp.status_code == 200
