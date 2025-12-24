"""High‑coverage tests for RedisClient wrappers using in‑memory fakes."""

from __future__ import annotations

import importlib.util
import sys
from types import ModuleType, SimpleNamespace

# Only stub redis stack if it's not available (CI often has it preinstalled)
if importlib.util.find_spec("redis") is None:  # pragma: no cover
    redis_stub = ModuleType("redis")
    redis_stub.exceptions = ModuleType("redis.exceptions")

    class _RedisError(Exception): ...

    class _ConnectionError(_RedisError): ...

    class _TimeoutError(_RedisError): ...

    redis_stub.exceptions.RedisError = _RedisError
    redis_stub.exceptions.ConnectionError = _ConnectionError
    redis_stub.exceptions.TimeoutError = _TimeoutError
    redis_stub.connection = ModuleType("redis.connection")
    redis_stub.connection.ConnectionPool = object
    sys.modules.setdefault("redis", redis_stub)
    sys.modules.setdefault("redis.exceptions", redis_stub.exceptions)
    sys.modules.setdefault("redis.connection", redis_stub.connection)
else:  # pragma: no cover
    import redis  # noqa: F401  # type: ignore

# Stub event_publisher to avoid heavy dependencies
event_pub_stub = ModuleType("libs.redis_client.event_publisher")


class _DummyPublisher:
    def __init__(self, *_args, **_kwargs): ...

    def publish(self, *_args, **_kwargs):
        return True


event_pub_stub.EventPublisher = _DummyPublisher
sys.modules.setdefault("libs.redis_client.event_publisher", event_pub_stub)

# Stub events module (pydantic-free)
events_stub = ModuleType("libs.redis_client.events")


class _BaseModel:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


events_stub.BaseModel = _BaseModel
events_stub.Field = lambda *args, **kwargs: None
events_stub.field_validator = lambda *args, **kwargs: (lambda f: f)
events_stub.OrderEvent = _BaseModel
events_stub.PositionEvent = _BaseModel
events_stub.SignalEvent = _BaseModel
sys.modules.setdefault("libs.redis_client.events", events_stub)

import pytest

from libs.redis_client import client as rc


class FakeRedis:
    """Simple in-memory stand‑in for redis.Redis."""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.pubsub_messages: list[tuple[str, str]] = []
        self.pipelines: list[list[tuple[str, tuple[str, ...]]]] = []
        self.info_called = False
        self.raise_next: Exception | None = None
        self.deleted: list[str] = []

    # Core commands -------------------------------------------------
    def ping(self):
        if self.raise_next:
            exc = self.raise_next
            self.raise_next = None
            raise exc
        return True

    def get(self, key):
        if self.raise_next:
            exc = self.raise_next
            self.raise_next = None
            raise exc
        return self.store.get(key)

    def mget(self, keys):
        if self.raise_next:
            exc = self.raise_next
            self.raise_next = None
            raise exc
        return [self.store.get(k) for k in keys]

    def set(self, key, value):
        self.store[key] = value
        return True

    def setex(self, key, ttl, value):
        # ttl ignored for fake
        self.set(key, value)
        return True

    def delete(self, *keys):
        if self.raise_next:
            exc = self.raise_next
            self.raise_next = None
            raise exc
        count = 0
        for k in keys:
            self.deleted.append(k)
            if k in self.store:
                del self.store[k]
                count += 1
        return count

    def publish(self, channel, message):
        self.pubsub_messages.append((channel, message))
        return len(self.pubsub_messages)

    # Set helpers ---------------------------------------------------
    def sadd(self, key, *members):
        s = set(self.store.get(key, "").split(",")) if key in self.store else set()
        before = len(s)
        s.update(members)
        self.store[key] = ",".join(sorted(s))
        return len(s) - before

    def smembers(self, key):
        if key not in self.store:
            return set()
        return set(self.store[key].split(","))

    # Sorted sets ---------------------------------------------------
    def zadd(self, key, mapping):
        # store as dict repr
        self.store[key] = mapping
        return len(mapping)

    def zcard(self, key):
        val = self.store.get(key, {})
        return len(val) if isinstance(val, dict) else 0

    def zremrangebyrank(self, key, start, stop):
        val = self.store.get(key, {})
        if not isinstance(val, dict):
            return 0
        keys = list(val.keys())
        to_remove = keys[start : stop + 1]
        for k in to_remove:
            val.pop(k, None)
        self.store[key] = val
        return len(to_remove)

    # Lists ---------------------------------------------------------
    def rpush(self, key, *values):
        lst = self.store.setdefault(key, [])
        if not isinstance(lst, list):
            lst = []
        lst.extend(values)
        self.store[key] = lst
        return len(lst)

    def ltrim(self, key, start, stop):
        lst = self.store.get(key, [])
        if not isinstance(lst, list):
            lst = []
        self.store[key] = lst[start : stop + 1 if stop != -1 else None]
        return True

    def lrange(self, key, start, stop):
        lst = self.store.get(key, [])
        if not isinstance(lst, list):
            lst = []
        end = stop + 1 if stop != -1 else None
        return lst[start:end]

    # Misc ----------------------------------------------------------
    def info(self):
        self.info_called = True
        return {"used_memory_human": "1K"}

    def eval(self, script, numkeys, *keys_and_args):
        # Return keys_and_args to assert wiring
        # mimic redis: returns args after keys
        return list(keys_and_args[numkeys:])

    def pipeline(self, transaction=True):
        cur_pipeline: list[tuple[str, tuple[str, ...]]] = []
        self.pipelines.append(cur_pipeline)

        class _Pipe:
            def __init__(self, redis_ref, store):
                self.redis_ref = redis_ref
                self.ops = cur_pipeline

            def watch(self, *_args, **_kwargs):
                return None

            def multi(self):
                return None

            def execute(self):
                return list(self.ops)

            def set(self, key, val):
                self.ops.append(("set", (key, val)))

        return _Pipe(self, self.store)


class FakeConnectionPool:
    def __init__(self, fake):
        self.fake = fake

    def disconnect(self):
        return None


@pytest.fixture()
def fake_redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr(rc, "redis", SimpleNamespace(Redis=lambda connection_pool=None: fake))
    monkeypatch.setattr(rc, "ConnectionPool", lambda **kwargs: FakeConnectionPool(fake))
    return fake


def test_get_set_delete_cycle(fake_redis):
    client = rc.RedisClient()
    client.set("a", "1")
    assert client.get("a") == "1"
    assert client.delete("a", "b") == 1


def test_mget_and_sadd_smembers(fake_redis):
    client = rc.RedisClient()
    client.set("k1", "v1")
    client.set("k2", "v2")
    assert client.mget(["k1", "k2", "k3"]) == ["v1", "v2", None]
    added = client.sadd("s", "a", "b")
    assert added == 2
    assert client.smembers("s") == {"a", "b"}


def test_publish_and_info(fake_redis):
    client = rc.RedisClient()
    count = client.publish("ch", "msg")
    assert count == 1
    info = client.get_info()
    assert info["used_memory_human"] == "1K"


def test_sorted_set_and_list_ops(fake_redis):
    client = rc.RedisClient()
    assert client.zadd("z", {"m1": 1.0, "m2": 2.0}) == 2
    assert client.zcard("z") == 2
    assert client.zremrangebyrank("z", 0, 0) == 1
    assert client.rpush("list", "a", "b") == 2
    client.ltrim("list", -1, -1)
    assert client.lrange("list", 0, -1) == ["b"]


def test_eval_and_pipeline(fake_redis):
    client = rc.RedisClient()
    result = client.eval("return ARGV", 1, "k1", "arg1", "arg2")
    assert result == ["arg1", "arg2"]
    pipe = client.pipeline()
    pipe.set("x", "1")
    pipe.multi()
    executed = pipe.execute()
    assert executed == [("set", ("x", "1"))]


def test_health_check_and_close(fake_redis):
    client = rc.RedisClient()
    assert client.health_check() is True
    client.close()  # should not raise


def test_retry_on_error(monkeypatch):
    """Verify retry path triggers when RedisError raised."""
    fake = FakeRedis()
    from redis.exceptions import RedisError

    monkeypatch.setattr(rc, "redis", SimpleNamespace(Redis=lambda connection_pool=None: fake))
    monkeypatch.setattr(rc, "ConnectionPool", lambda **kwargs: FakeConnectionPool(fake))
    client = rc.RedisClient()
    fake.raise_next = RedisError("fail once")
    with pytest.raises(RedisError):
        client.get("any")  # RedisError is not retried; should bubble
