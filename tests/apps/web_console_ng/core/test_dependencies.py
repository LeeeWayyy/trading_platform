from __future__ import annotations

import sys
import types
from typing import Any

import pytest

import apps.web_console_ng.core.dependencies as dependencies
from apps.web_console_ng import config


class DummyPool:
    def __init__(self, dsn: str, min_size: int, max_size: int) -> None:
        self.dsn = dsn
        self.min_size = min_size
        self.max_size = max_size
        self.closed = False

    def close(self) -> None:
        self.closed = True


class DummyRedis:
    def __init__(self, url: str, decode_responses: bool) -> None:
        self.url = url
        self.decode_responses = decode_responses
        self.closed = False

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def reset_singletons(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dependencies, "_sync_db_pool", None)
    monkeypatch.setattr(dependencies, "_sync_redis_client", None)


def _install_dummy_psycopg(monkeypatch: pytest.MonkeyPatch) -> None:
    module = types.SimpleNamespace(ConnectionPool=DummyPool)
    monkeypatch.setitem(sys.modules, "psycopg_pool", module)


def _install_dummy_redis(monkeypatch: pytest.MonkeyPatch, *, instance: DummyRedis) -> None:
    class DummyRedisModule:
        @staticmethod
        def from_url(url: str, decode_responses: bool) -> DummyRedis:
            return instance

    module = types.SimpleNamespace(Redis=DummyRedisModule)
    monkeypatch.setitem(sys.modules, "redis", module)


def test_get_sync_db_pool_requires_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(RuntimeError, match="DATABASE_URL not set"):
        dependencies.get_sync_db_pool()


def test_get_sync_db_pool_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_dummy_psycopg(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgres://db")
    monkeypatch.setenv("SYNC_DB_POOL_MIN_SIZE", "2")
    monkeypatch.setenv("SYNC_DB_POOL_MAX_SIZE", "4")

    pool = dependencies.get_sync_db_pool()
    assert isinstance(pool, DummyPool)
    assert pool.dsn == "postgres://db"
    assert pool.min_size == 2
    assert pool.max_size == 4

    again = dependencies.get_sync_db_pool()
    assert again is pool


def test_close_sync_db_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_dummy_psycopg(monkeypatch)
    monkeypatch.setenv("DATABASE_URL", "postgres://db")

    pool = dependencies.get_sync_db_pool()
    dependencies.close_sync_db_pool()

    assert pool.closed is True
    assert dependencies._sync_db_pool is None


def test_get_sync_redis_client_requires_url(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy = DummyRedis("redis://", decode_responses=True)
    _install_dummy_redis(monkeypatch, instance=dummy)
    monkeypatch.setattr(config, "REDIS_URL", "")

    with pytest.raises(RuntimeError, match="REDIS_URL not set"):
        dependencies.get_sync_redis_client()


def test_get_sync_redis_client_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy = DummyRedis("redis://localhost:6379/1", decode_responses=True)
    _install_dummy_redis(monkeypatch, instance=dummy)
    monkeypatch.setattr(config, "REDIS_URL", "redis://localhost:6379/1")

    client = dependencies.get_sync_redis_client()
    assert client is dummy
    assert client.decode_responses is True

    again = dependencies.get_sync_redis_client()
    assert again is client


def test_close_sync_redis_client(monkeypatch: pytest.MonkeyPatch) -> None:
    dummy = DummyRedis("redis://localhost:6379/1", decode_responses=True)
    _install_dummy_redis(monkeypatch, instance=dummy)
    monkeypatch.setattr(config, "REDIS_URL", "redis://localhost:6379/1")

    client = dependencies.get_sync_redis_client()
    dependencies.close_sync_redis_client()

    assert client.closed is True
    assert dependencies._sync_redis_client is None
