"""Database connection helpers for psycopg3-style pools.

This adapter provides a uniform async context manager for
psycopg_pool.AsyncConnectionPool instances and direct async connection-like
objects used in tests. Synchronous pools are rejected to avoid returning
blocking connections to async callers. It explicitly rejects asyncpg-style
pools to avoid placeholder mismatches ($1 vs %s).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any


@asynccontextmanager
async def acquire_connection(db_pool: Any) -> AsyncIterator[Any]:
    """Acquire a connection in an async context-friendly way.

    Supports:
    - psycopg_pool.AsyncConnectionPool (``connection()`` returns async CM)
    - Connection-like objects exposing ``execute()`` (e.g., async test doubles)

    Rejects asyncpg pools to prevent SQL placeholder mismatches.
    """
    # psycopg_pool pools expose connection() returning context manager
    if hasattr(db_pool, "connection"):
        candidate = db_pool.connection()
        if hasattr(candidate, "__aenter__"):
            async with candidate as conn:
                yield conn
            return
        if hasattr(candidate, "__enter__"):
            raise RuntimeError(
                "Synchronous connection pools are not supported; use "
                "psycopg_pool.AsyncConnectionPool or an async connection."
            )
        raise RuntimeError("Unsupported connection context manager on db_pool.connection()")

    # asyncpg pools expose ``acquire``; reject to avoid $1 placeholder usage
    if hasattr(db_pool, "acquire"):
        raise RuntimeError(
            "asyncpg pools are not supported; use psycopg_pool.AsyncConnectionPool "
            "with psycopg3-style %s placeholders"
        )

    # Direct connection or mock used in tests
    if hasattr(db_pool, "execute"):
        yield db_pool
        return

    raise RuntimeError(
        "Unsupported db_pool interface. Expected psycopg_pool.AsyncConnectionPool "
        "or a connection-like object with execute()."
    )


__all__ = ["acquire_connection"]
