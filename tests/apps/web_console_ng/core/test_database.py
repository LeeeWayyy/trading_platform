"""Tests for async database pool helpers."""

from __future__ import annotations

import logging
import sys
import types

import pytest

from apps.web_console_ng.core import database


class DummyPool:
    def __init__(self, dsn: str, min_size: int, max_size: int, timeout: float, open: bool) -> None:
        self.dsn = dsn
        self.min_size = min_size
        self.max_size = max_size
        self.timeout = timeout
        self.open = open
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def reset_pool() -> None:
    database.set_db_pool(None)


def test_init_db_pool_no_dsn(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    caplog.set_level(logging.INFO, logger=database.logger.name)

    pool = database.init_db_pool()

    assert pool is None
    assert database.get_db_pool() is None
    assert any("DATABASE_URL not set" in record.message for record in caplog.records)


def test_init_db_pool_missing_dependency(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    fake_module = types.ModuleType("psycopg_pool")
    monkeypatch.setitem(sys.modules, "psycopg_pool", fake_module)
    caplog.set_level(logging.WARNING, logger=database.logger.name)

    pool = database.init_db_pool()

    assert pool is None
    assert database.get_db_pool() is None
    assert any("psycopg_pool not installed" in record.message for record in caplog.records)


def test_init_db_pool_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    monkeypatch.setenv("DB_POOL_MIN_SIZE", "2")
    monkeypatch.setenv("DB_POOL_MAX_SIZE", "7")
    monkeypatch.setenv("DB_POOL_TIMEOUT", "12.5")

    module = types.ModuleType("psycopg_pool")
    module.AsyncConnectionPool = DummyPool
    monkeypatch.setitem(sys.modules, "psycopg_pool", module)

    pool = database.init_db_pool()

    assert isinstance(pool, DummyPool)
    assert pool.dsn == "postgresql://user:pass@localhost/db"
    assert pool.min_size == 2
    assert pool.max_size == 7
    assert pool.timeout == 12.5
    assert pool.open is False
    assert database.get_db_pool() is pool


def test_set_db_pool_and_get_db_pool() -> None:
    pool = DummyPool("dsn", 1, 2, 3.0, False)
    database.set_db_pool(pool)

    assert database.get_db_pool() is pool


@pytest.mark.asyncio()
async def test_close_db_pool() -> None:
    pool = DummyPool("dsn", 1, 2, 3.0, False)
    database.set_db_pool(pool)

    await database.close_db_pool()

    assert pool.closed is True
    assert database.get_db_pool() is None
