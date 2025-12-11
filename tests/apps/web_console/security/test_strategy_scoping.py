import base64
import os

import pytest

from apps.web_console.data.strategy_scoped_queries import StrategyScopedDataAccess


# Generate a valid 32-byte encryption key for tests
_TEST_ENCRYPTION_KEY = base64.b64encode(os.urandom(32)).decode()


@pytest.fixture(autouse=True)
def set_cache_encryption_key(monkeypatch):
    """Set encryption key so caching works in tests."""
    monkeypatch.setenv("STRATEGY_CACHE_ENCRYPTION_KEY", _TEST_ENCRYPTION_KEY)


class FakeCursor:
    def __init__(self, query, params):
        self.query = query
        self.params = params

    async def fetchall(self):
        strategies = self.params[0]
        return [{"strategy_id": s, "value": 1} for s in strategies]

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class FakeConn:
    def __init__(self):
        self.calls = []

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, query, params):
        self.calls.append((query.strip(), params))
        return FakeCursor(query.strip(), params)


class FakeRedis:
    def __init__(self):
        self.store = {}

    async def get(self, key):
        return self.store.get(key)

    async def setex(self, key, ttl, value):
        self.store[key] = value


@pytest.mark.asyncio
async def test_strategy_scoping_enforces_access():
    pool = FakeConn()
    redis = FakeRedis()
    user = {"user_id": "u1", "role": "viewer", "strategies": ["alpha"]}
    scoped = StrategyScopedDataAccess(pool, redis, user)

    rows = await scoped.get_positions()
    assert rows[0]["strategy_id"] == "alpha"
    # Cached second call
    _ = await scoped.get_positions()
    assert len(pool.calls) == 1


@pytest.mark.asyncio
async def test_strategy_scoping_denies_empty():
    pool = FakeConn()
    redis = FakeRedis()
    user = {"user_id": "u1", "role": "viewer", "strategies": []}
    scoped = StrategyScopedDataAccess(pool, redis, user)
    with pytest.raises(PermissionError):
        await scoped.get_positions()


@pytest.mark.asyncio
async def test_strategy_scoping_applies_filters_in_queries():
    pool = FakeConn()
    redis = FakeRedis()
    user = {"user_id": "u1", "role": "viewer", "strategies": ["alpha", "beta"]}
    scoped = StrategyScopedDataAccess(pool, redis, user)

    await scoped.get_orders(symbol="AAPL", side="buy")

    recorded_query, params = pool.calls[-1]

    assert "symbol = %s" in recorded_query
    assert "side = %s" in recorded_query
    assert params[1] == "AAPL"
    assert params[2] == "buy"


@pytest.mark.asyncio
async def test_strategy_scoping_cache_keys_include_filters():
    pool = FakeConn()
    redis = FakeRedis()
    user = {"user_id": "u1", "role": "viewer", "strategies": ["alpha"]}
    scoped = StrategyScopedDataAccess(pool, redis, user)

    await scoped.get_positions(symbol="AAPL")
    await scoped.get_positions(symbol="MSFT")

    # Different filters should bypass cache and trigger separate fetches
    assert len(pool.calls) == 2
