"""Synchronous dependencies for legacy services in NiceGUI web console.

This module provides sync DB pool and sync Redis client for legacy services
that haven't been migrated to async (e.g., CircuitBreakerService, BacktestJobQueue).

⚠️ CRITICAL: Use these ONLY for legacy sync services.
For new async services, use core/database.py (async pool) and core/redis_ha.py.

Usage:
    from apps.web_console_ng.core.dependencies import get_sync_db_pool, get_sync_redis_client

    # In service factory
    sync_pool = get_sync_db_pool()
    sync_redis = get_sync_redis_client()
    service = CircuitBreakerService(db_pool=sync_pool, redis_client=sync_redis)

Shutdown:
    Call close_sync_db_pool() and close_sync_redis_client() from main.py shutdown.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from psycopg_pool import ConnectionPool
    from redis import Redis

logger = logging.getLogger(__name__)

# Module-level references with explicit type hints for lazy initialization
_sync_db_pool: ConnectionPool | None = None
_sync_redis_client: Redis | None = None


def get_sync_db_pool() -> ConnectionPool:
    """Get synchronous DB pool for legacy services.

    Uses psycopg_pool.ConnectionPool (sync) for compatibility with existing
    code that uses `with pool.connection():` context manager syntax.

    Returns:
        ConnectionPool instance.

    Raises:
        RuntimeError: If DATABASE_URL is not set.
        ImportError: If psycopg_pool is not installed.
    """
    global _sync_db_pool

    if _sync_db_pool is None:
        dsn = os.getenv("DATABASE_URL")
        if not dsn:
            raise RuntimeError("DATABASE_URL not set - sync DB pool unavailable")

        from psycopg_pool import ConnectionPool

        min_size = int(os.getenv("SYNC_DB_POOL_MIN_SIZE", "1"))
        max_size = int(os.getenv("SYNC_DB_POOL_MAX_SIZE", "5"))

        _sync_db_pool = ConnectionPool(dsn, min_size=min_size, max_size=max_size)
        logger.info("Sync DB pool initialized (min=%d, max=%d)", min_size, max_size)

    return _sync_db_pool


def get_sync_redis_client() -> Redis:
    """Get synchronous Redis client for legacy services.

    Used by CircuitBreakerService, BacktestJobQueue progress tracking,
    and other legacy sync services.

    Returns:
        Redis instance.

    Raises:
        RuntimeError: If REDIS_URL is not set.
    """
    global _sync_redis_client

    if _sync_redis_client is None:
        from redis import Redis

        from apps.web_console_ng import config

        redis_url = config.REDIS_URL
        if not redis_url:
            raise RuntimeError("REDIS_URL not set - sync Redis client unavailable")

        _sync_redis_client = Redis.from_url(redis_url, decode_responses=True)
        logger.info("Sync Redis client initialized from %s", redis_url.split("@")[-1])

    return _sync_redis_client


def close_sync_db_pool() -> None:
    """Close sync DB pool on shutdown.

    Call from main.py shutdown hook.
    """
    global _sync_db_pool

    if _sync_db_pool is not None:
        _sync_db_pool.close()
        _sync_db_pool = None
        logger.info("Sync DB pool closed")


def close_sync_redis_client() -> None:
    """Close sync Redis client on shutdown.

    Call from main.py shutdown hook.
    """
    global _sync_redis_client

    if _sync_redis_client is not None:
        _sync_redis_client.close()
        _sync_redis_client = None
        logger.info("Sync Redis client closed")
