from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

# Skip if pydantic isn't present (client package transitively imports it via event_publisher)
pytest.importorskip("pydantic")

# Stub redis + jwt before importing client module to avoid heavy deps
redis_stub = ModuleType("redis")
redis_stub.asyncio = ModuleType("redis.asyncio")

class _ConnectionPool:
    def __init__(self, *args, **kwargs):
        pass

    def disconnect(self):
        return None


class _RedisError(Exception):
    pass


redis_stub.connection = ModuleType("redis.connection")
redis_stub.connection.ConnectionPool = _ConnectionPool
redis_stub.exceptions = ModuleType("redis.exceptions")
redis_stub.exceptions.RedisError = _RedisError
redis_stub.exceptions.ConnectionError = _RedisError
redis_stub.exceptions.TimeoutError = _RedisError
class _Redis:
    def __init__(self, *args, **kwargs):
        pass
    def ping(self):
        return True
redis_stub.Redis = _Redis

sys.modules.setdefault("redis", redis_stub)
sys.modules.setdefault("redis.asyncio", redis_stub.asyncio)
sys.modules.setdefault("redis.connection", redis_stub.connection)
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

import libs.redis_client.client as client_mod


class FakeRedis:
    def __init__(self, *args: Any, **kwargs: Any):
        self.store: dict[str, set[str]] = {}
        self.deleted: list[tuple[str, ...]] = []

    # Connection check
    def ping(self) -> bool:  # pragma: no cover - trivial but keeps init happy
        return True

    def sadd(self, key: str, *members: str) -> int:
        self.store.setdefault(key, set())
        before = len(self.store[key])
        self.store[key].update(members)
        return len(self.store[key]) - before

    def smembers(self, key: str):
        return set(self.store.get(key, set()))

    def delete(self, *keys: str) -> int:
        self.deleted.append(keys)
        removed = 0
        for k in keys:
            if k in self.store:
                del self.store[k]
                removed += 1
        return removed

    def pipeline(self, transaction: bool = True):
        # Return a lightweight pipeline that records calls
        return SimpleNamespace(transaction=transaction)

    # Not used but required by RedisClient.mget/set for completeness
    def mget(self, keys):
        return [None for _ in keys]

    def set(self, *args, **kwargs):
        return True

    def setex(self, *args, **kwargs):
        return True


class FakePool:
    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs

    def disconnect(self):
        return None


@pytest.fixture(autouse=True)
def patch_redis(monkeypatch):
    monkeypatch.setattr(client_mod, "ConnectionPool", FakePool)
    monkeypatch.setattr(client_mod.redis, "Redis", FakeRedis)
    yield


def test_sadd_and_smembers_round_trip():
    client = client_mod.RedisClient()
    added = client.sadd("k1", "a", "b")
    assert added == 2
    assert client.smembers("k1") == {"a", "b"}


def test_delete_handles_multiple_keys():
    client = client_mod.RedisClient()
    client.sadd("k1", "a")
    client.sadd("k2", "b")
    deleted = client.delete("k1", "k2", "missing")
    assert deleted == 2


def test_pipeline_passthrough():
    client = client_mod.RedisClient()
    pipe = client.pipeline(transaction=False)
    assert getattr(pipe, "transaction") is False
