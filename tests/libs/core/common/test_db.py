"""
Unit tests for libs.core.common.db.

Covers:
- acquire_connection with async pool context manager
- rejection of sync pools or unsupported interfaces
- rejection of asyncpg-style pools
- direct connection-like objects
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from libs.core.common.db import acquire_connection


class _AsyncConnCM:
    def __init__(self, conn):
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _SyncConnCM:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class _AsyncPool:
    def __init__(self, conn):
        self._conn = conn

    def connection(self):
        return _AsyncConnCM(self._conn)


class _SyncPool:
    def connection(self):
        return _SyncConnCM()


class _BadPool:
    def connection(self):
        return object()


class _AsyncpgPool:
    def acquire(self):
        return AsyncMock()


class _DirectConn:
    async def execute(self, *_args, **_kwargs):
        return None


@pytest.mark.asyncio()
async def test_acquire_connection_from_async_pool():
    """AsyncConnectionPool-style context manager yields connection."""
    conn = AsyncMock()
    pool = _AsyncPool(conn)

    async with acquire_connection(pool) as acquired:
        assert acquired is conn


@pytest.mark.asyncio()
async def test_acquire_connection_direct_connection_object():
    """Direct connection-like objects are yielded as-is."""
    conn = _DirectConn()

    async with acquire_connection(conn) as acquired:
        assert acquired is conn


@pytest.mark.asyncio()
async def test_acquire_connection_rejects_sync_pool():
    """Synchronous pool connection context managers are rejected."""
    pool = _SyncPool()

    with pytest.raises(RuntimeError, match="Synchronous connection pools are not supported"):
        async with acquire_connection(pool):
            pass


@pytest.mark.asyncio()
async def test_acquire_connection_rejects_bad_connection_context():
    """Unknown connection context manager types raise clear errors."""
    pool = _BadPool()

    with pytest.raises(RuntimeError, match="Unsupported connection context manager"):
        async with acquire_connection(pool):
            pass


@pytest.mark.asyncio()
async def test_acquire_connection_rejects_asyncpg_pool():
    """asyncpg-style pools are rejected to avoid placeholder mismatches."""
    pool = _AsyncpgPool()

    with pytest.raises(RuntimeError, match="asyncpg pools are not supported"):
        async with acquire_connection(pool):
            pass


@pytest.mark.asyncio()
async def test_acquire_connection_rejects_unknown_interface():
    """Unknown db_pool interfaces are rejected."""
    with pytest.raises(RuntimeError, match="Unsupported db_pool interface"):
        async with acquire_connection(object()):
            pass
