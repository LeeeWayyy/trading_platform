from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pytest

pytest.importorskip("pydantic")

# Minimal redis stub
redis_stub = ModuleType("redis")
redis_stub.exceptions = ModuleType("redis.exceptions")
class _RedisError(Exception):
    pass
redis_stub.exceptions.RedisError = _RedisError
redis_stub.exceptions.ConnectionError = _RedisError
redis_stub.exceptions.TimeoutError = _RedisError


class _ConnectionPool:
    def __init__(self, *args, **kwargs):
        pass
    def disconnect(self):
        return None


class FakeRedis:
    def __init__(self, *args, **kwargs):
        self.deleted = []
    def ping(self):
        return True
    def delete(self, *keys):
        self.deleted.append(keys)
        return len(keys)
    def pipeline(self, transaction=True):
        return SimpleNamespace(transaction=transaction)


redis_stub.connection = ModuleType("redis.connection")
redis_stub.connection.ConnectionPool = _ConnectionPool
redis_stub.Redis = FakeRedis
sys.modules.setdefault("redis", redis_stub)
sys.modules.setdefault("redis.exceptions", redis_stub.exceptions)
sys.modules.setdefault("redis.connection", redis_stub.connection)

# jwt stub to satisfy imports
jwt_stub = ModuleType("jwt")
jwt_stub.api_jwk = SimpleNamespace(PyJWK=None, PyJWKSet=None)
jwt_stub.algorithms = SimpleNamespace(
    get_default_algorithms=lambda: {}, has_crypto=lambda: False, requires_cryptography=False
)
jwt_stub.utils = SimpleNamespace()
sys.modules.setdefault("jwt", jwt_stub)
sys.modules.setdefault("jwt.api_jwk", jwt_stub.api_jwk)
sys.modules.setdefault("jwt.algorithms", jwt_stub.algorithms)
sys.modules.setdefault("jwt.utils", jwt_stub.utils)

from libs.redis_client.client import RedisClient


def test_delete_multiple_keys_returns_count():
    client = RedisClient()
    deleted = client.delete("a", "b", "c")
    assert deleted == 3


def test_pipeline_respects_transaction_flag():
    client = RedisClient()
    pipe = client.pipeline(transaction=False)
    assert pipe.transaction is False
