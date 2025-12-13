"""Additional RedisClient edge‑case coverage."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from libs.redis_client import client as rc


class Boom(Exception):
    """Marker exception for failure paths."""


class _FlakyRedis:
    """Redis stub that fails first call then succeeds."""

    def __init__(self):
        self.calls = 0

    def ping(self):
        self.calls += 1
        if self.calls == 1:
            from redis.exceptions import ConnectionError

            raise ConnectionError("transient")
        return True

    def get(self, key):
        return None


class _FailingPool:
    """Pool stub that raises on init to hit RedisConnectionError."""

    def __init__(self, *args, **kwargs):
        from redis.exceptions import ConnectionError

        raise ConnectionError("no redis")


def test_init_failure_raises(monkeypatch):
    """Verify RedisConnectionError when initial ping fails."""

    monkeypatch.setattr(rc, "ConnectionPool", _FailingPool)
    with pytest.raises(rc.RedisConnectionError):
        rc.RedisClient()


def test_health_check_false(monkeypatch):
    class _HealthyThenFail:
        def __init__(self):
            self.fail = False

        def ping(self):
            if self.fail:
                from redis.exceptions import RedisError

                raise RedisError("down")
            return True

    fake = _HealthyThenFail()
    monkeypatch.setattr(rc, "redis", SimpleNamespace(Redis=lambda connection_pool=None: fake))
    monkeypatch.setattr(rc, "ConnectionPool", lambda **kwargs: object())
    client = rc.RedisClient()
    fake.fail = True
    assert client.health_check() is False


def test_set_with_ttl_and_delete_no_keys(monkeypatch):
    class _MiniRedis:
        def __init__(self):
            self.store = {}

        def ping(self):
            return True

        def setex(self, key, ttl, value):
            self.store[(key, ttl)] = value
            return True

        def delete(self, *keys):
            return 0

    mini = _MiniRedis()
    monkeypatch.setattr(rc, "redis", SimpleNamespace(Redis=lambda connection_pool=None: mini))
    monkeypatch.setattr(rc, "ConnectionPool", lambda **kwargs: object())

    client = rc.RedisClient()
    client.set("k", "v", ttl=5)
    assert mini.store[("k", 5)] == "v"
    assert client.delete() == 0


def test_retry_on_connection_error(monkeypatch):
    """Ensure retry wrapper retries connection errors on get()."""

    class _ConnFlap:
        def __init__(self):
            self.calls = 0

        def ping(self):
            return True

        def get(self, key):
            from redis.exceptions import ConnectionError

            self.calls += 1
            if self.calls < 2:
                raise ConnectionError("flap")
            return "ok"

    flap = _ConnFlap()
    monkeypatch.setattr(rc, "redis", SimpleNamespace(Redis=lambda connection_pool=None: flap))
    monkeypatch.setattr(rc, "ConnectionPool", lambda **kwargs: object())

    client = rc.RedisClient()
    assert client.get("x") == "ok"
