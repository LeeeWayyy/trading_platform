"""Database and Redis connection utilities.

This module provides connection adapters that are safe to use with async context.
The key design principle is:

- **Cacheable config, fresh connections per call**: The adapters store only
  configuration (URLs, timeouts) and create fresh connections for each request.
  This avoids event loop binding issues with psycopg3 AsyncConnectionPool and
  redis.asyncio when used in environments creating fresh event loops.

Usage:
    from libs.core.common.db_pool import get_db_pool, get_redis_client

    # In a service or page
    db_adapter = get_db_pool()
    redis_adapter = get_redis_client()

    scoped_access = StrategyScopedDataAccess(
        db_pool=db_adapter,
        redis_client=redis_adapter,
        user=dict(user),
    )
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache
from typing import TYPE_CHECKING, Any

# Configuration from environment
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://trader:trader@localhost:5433/trader")
DATABASE_CONNECT_TIMEOUT = int(os.getenv("DATABASE_CONNECT_TIMEOUT", "2"))

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class AsyncConnectionAdapter:
    """Adapter providing fresh async connections per call.

    This adapter is safe to cache with @lru_cache because it only
    holds configuration (DATABASE_URL), not an actual connection pool. Each
    call to connection() creates a fresh psycopg.AsyncConnection bound to the
    current event loop.

    The adapter implements the same interface as psycopg_pool.AsyncConnectionPool
    (i.e., .connection() returning an async context manager), so it's compatible
    with the existing acquire_connection() helper.
    """

    def __init__(self, database_url: str, connect_timeout: float = 5.0) -> None:
        """Initialize the adapter with connection configuration.

        Args:
            database_url: PostgreSQL connection string
            connect_timeout: Connection timeout in seconds
        """
        self._database_url = database_url
        self._connect_timeout = connect_timeout

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[Any]:
        """Create a fresh async connection for each request.

        The connection is created and closed within the same event loop
        context.

        Uses row_factory=dict_row so queries return dicts (required by
        StrategyScopedDataAccess which casts rows with dict(row)).

        Yields:
            psycopg.AsyncConnection instance with dict_row factory
        """
        import psycopg
        from psycopg.rows import dict_row

        async with await psycopg.AsyncConnection.connect(
            self._database_url,
            # psycopg type stubs require int for connect_timeout (not float)
            connect_timeout=int(self._connect_timeout),
            row_factory=dict_row,
        ) as conn:
            yield conn


class AsyncRedisAdapter:
    """Adapter providing fresh async Redis connections per call.

    Similar to AsyncConnectionAdapter, this adapter stores only configuration
    and creates fresh Redis connections for each async context. This avoids
    event loop binding issues.

    The adapter provides a compatible interface for StrategyScopedDataAccess
    which expects redis.asyncio.Redis-like objects.
    """

    def __init__(self, redis_url: str, db: int = 3) -> None:
        """Initialize the adapter with Redis configuration.

        Args:
            redis_url: Redis connection URL
            db: Database index for cache isolation (default: 3)
        """
        self._redis_url = redis_url
        self._db = db

    @asynccontextmanager
    async def client(self) -> AsyncIterator[Any]:
        """Create a fresh async Redis client for each request.

        Yields:
            redis.asyncio.Redis instance
        """
        import redis.asyncio as redis_asyncio

        client = redis_asyncio.Redis.from_url(self._redis_url, db=self._db)
        try:
            yield client
        finally:
            # Note: aclose() is the correct async close method in redis.asyncio 5.0+
            # (close() is sync-only, aclose() is the async equivalent)
            await client.aclose()

    # Proxy methods to make adapter usable directly as a Redis client.
    # Design decision: Using proxy pattern instead of requiring callers to use
    # `async with self.redis.client() as client:` because:
    # 1. Maintains compatibility with real redis.asyncio.Redis clients (not just this adapter)
    # 2. Minimizes changes to StrategyScopedDataAccess (only uses get/setex)
    # 3. Each proxy method creates a fresh connection, avoiding event loop binding issues

    async def get(self, key: str) -> bytes | None:
        """Get value from Redis."""
        async with self.client() as client:
            result: bytes | None = await client.get(key)
            return result

    async def set(self, key: str, value: str | bytes, ex: int | None = None) -> bool | None:
        """Set value in Redis with optional expiration."""
        async with self.client() as client:
            result: bool | None = await client.set(key, value, ex=ex)
            return result

    async def setex(self, key: str, time: int, value: str | bytes) -> bool | None:
        """Set value in Redis with expiration time in seconds.

        This is the method used by StrategyScopedDataAccess._set_cached.
        """
        async with self.client() as client:
            result: bool | None = await client.setex(key, time, value)
            return result

    async def delete(self, *keys: str) -> int:
        """Delete keys from Redis."""
        async with self.client() as client:
            result: int = await client.delete(*keys)
            return result

    async def exists(self, *keys: str) -> int:
        """Check if keys exist in Redis."""
        async with self.client() as client:
            result: int = await client.exists(*keys)
            return result


@lru_cache(maxsize=1)
def get_db_pool() -> AsyncConnectionAdapter | None:
    """Get database connection adapter (cacheable config, fresh connections per call).

    The adapter is cached via @lru_cache for efficiency, but each
    database connection is created fresh within the async context to avoid
    event loop binding issues.

    Returns:
        AsyncConnectionAdapter that creates fresh connections, or None if
        DATABASE_URL is not configured or initialization fails.
    """
    database_url = DATABASE_URL
    if not database_url:
        logger.warning(
            "db_pool_not_configured",
            extra={"reason": "DATABASE_URL not set"},
        )
        return None

    try:
        adapter = AsyncConnectionAdapter(
            database_url,
            connect_timeout=DATABASE_CONNECT_TIMEOUT,
        )
        logger.info(
            "db_adapter_initialized",
            extra={"connect_timeout": DATABASE_CONNECT_TIMEOUT},
        )
        return adapter
    except (ImportError, TypeError, ValueError) as e:
        # SILENT: Add structured logging for adapter initialization failures
        # ImportError: psycopg not installed
        # TypeError: Invalid database_url or connect_timeout type
        # ValueError: Invalid connection parameters
        logger.exception(
            "db_adapter_init_failed",
            extra={
                "error": str(e),
                "error_type": type(e).__name__,
                "database_url_configured": bool(database_url),
            },
        )
        return None


@lru_cache(maxsize=1)
def get_redis_client() -> AsyncRedisAdapter | None:
    """Get async Redis adapter for strategy cache (DB=3 for isolation).

    Configuration Source: REDIS_URL environment variable (same as session store)
    with DB index overridden to REDIS_STRATEGY_CACHE_DB (default=3) for cache
    isolation from session data.

    The adapter is cached via @lru_cache, but each Redis connection
    is created fresh within the async context to avoid event loop binding issues.

    Returns:
        AsyncRedisAdapter that creates fresh connections, or None if
        REDIS_URL is not configured.
    """
    redis_url = os.getenv("REDIS_URL")
    if not redis_url:
        logger.warning(
            "redis_client_not_configured",
            extra={"reason": "REDIS_URL not set, caching disabled"},
        )
        return None

    try:
        cache_db = int(os.getenv("REDIS_STRATEGY_CACHE_DB", "3"))

        adapter = AsyncRedisAdapter(redis_url, db=cache_db)
        logger.info(
            "redis_adapter_initialized",
            extra={"cache_db": cache_db},
        )
        return adapter
    except (ImportError, TypeError, ValueError, OSError) as e:
        # SILENT: Add structured logging for Redis adapter initialization failures
        # ImportError: redis.asyncio not installed
        # TypeError: Invalid redis_url or db type
        # ValueError: Invalid connection parameters (e.g., negative db index)
        # OSError: Redis connection errors during adapter creation
        logger.exception(
            "redis_adapter_init_failed",
            extra={
                "error": str(e),
                "error_type": type(e).__name__,
                "redis_url_configured": bool(redis_url),
                "cache_db": cache_db,
            },
        )
        return None



__all__ = [
    "AsyncConnectionAdapter",
    "AsyncRedisAdapter",
    "get_db_pool",
    "get_redis_client",
]
