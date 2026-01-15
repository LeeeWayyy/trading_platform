"""Synchronous database and Redis connection pools for BacktestJobQueue.

This module provides sync connection pools separate from the async pools in db_pool.py.
BacktestJobQueue uses synchronous `with pool.connection():` syntax, requiring a sync pool.

Design Notes:
- Uses lru_cache to persist pools (singleton pattern)
- Small max_size (5) to avoid resource contention
- Redis URL built from env vars for container compatibility
"""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from functools import lru_cache
from typing import TYPE_CHECKING

import redis
from psycopg_pool import ConnectionPool

if TYPE_CHECKING:
    from libs.trading.backtest.job_queue import BacktestJobQueue


def _get_redis_url() -> str:
    """Build Redis URL from environment, falling back to container defaults.

    Priority:
    1. REDIS_URL env var (if set)
    2. Build from REDIS_HOST/PORT/DB (container-compatible defaults)

    Returns:
        Redis connection URL string
    """
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        return redis_url
    # Fallback: build from individual vars (container-compatible)
    host = os.getenv("REDIS_HOST", "redis")  # 'redis' is docker service name
    port = os.getenv("REDIS_PORT", "6379")
    db = os.getenv("REDIS_DB", "0")
    return f"redis://{host}:{port}/{db}"


@lru_cache(maxsize=1)
def get_sync_db_pool() -> ConnectionPool:
    """Get synchronous psycopg connection pool for BacktestJobQueue.

    CRITICAL: This is separate from the async pool in db_pool.py because
    BacktestJobQueue uses synchronous `with pool.connection():` syntax.

    The pool is cached via lru_cache to persist as a singleton.
    Uses small max_size to avoid resource contention.

    Returns:
        ConnectionPool: Sync psycopg connection pool

    Raises:
        RuntimeError: If DATABASE_URL is not configured
    """
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL not configured")
    pool = ConnectionPool(conninfo=database_url, min_size=1, max_size=5)
    pool.open()
    return pool


@lru_cache(maxsize=1)
def get_sync_redis_client() -> redis.Redis:
    """Get synchronous Redis client for BacktestJobQueue.

    CRITICAL: Separate from async Redis adapter in db_pool.py.
    Uses _get_redis_url() for container compatibility (not hardcoded localhost).

    Returns:
        Redis: Sync Redis client
    """
    return redis.Redis.from_url(_get_redis_url())


@contextmanager
def get_job_queue() -> Generator[BacktestJobQueue, None, None]:
    """Get BacktestJobQueue with sync psycopg connection pool.

    Usage:
        with get_job_queue() as queue:
            job = queue.enqueue(config, priority=priority, created_by=username)

    Yields:
        BacktestJobQueue: Configured job queue instance

    Note:
        Does not close the cached pool - reuses singleton.
    """
    # Import here to avoid circular imports
    from libs.trading.backtest.job_queue import BacktestJobQueue

    redis_client = get_sync_redis_client()
    pool = get_sync_db_pool()
    queue = BacktestJobQueue(redis_client, pool)
    try:
        yield queue
    finally:
        # Do not close the cached pool; reuses this singleton
        pass


__all__ = [
    "get_sync_db_pool",
    "get_sync_redis_client",
    "get_job_queue",
]
