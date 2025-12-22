"""Shared Redis utilities for web console components.

This module provides async Redis client utilities that handle event loop
binding issues in Streamlit's execution model.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import redis.asyncio as redis_async
from redis.exceptions import RedisError

logger = logging.getLogger(__name__)


@asynccontextmanager
async def async_redis_client() -> AsyncIterator[redis_async.Redis | None]:
    """Create a fresh async Redis client for this async context.

    IMPORTANT: Async Redis clients bind to the event loop at first use.
    Streamlit's run_async() creates a new event loop per call, so we MUST
    create the client inside the async context (not pass it from sync code).

    This context manager ensures:
    1. Client is created inside the same event loop where it's used
    2. Client is properly closed after use (preventing connection leaks)
    3. Graceful degradation if Redis is unavailable

    Usage:
        async with async_redis_client() as redis:
            if redis:
                await redis.get("key")

    Yields:
        Fresh async Redis client or None if connection fails
    """
    host = os.getenv("REDIS_HOST", "localhost")
    port_str = os.getenv("REDIS_PORT", "6379")
    db_str = os.getenv("REDIS_DB", "0")

    try:
        port = int(port_str)
        db = int(db_str)
    except (ValueError, TypeError):
        logger.warning("Invalid REDIS_PORT or REDIS_DB env vars")
        yield None
        return

    password = os.getenv("REDIS_PASSWORD") or None
    client: redis_async.Redis | None = None
    try:
        client = redis_async.Redis(
            host=host, port=port, db=db, password=password, decode_responses=True
        )
        yield client
    except (RedisError, ConnectionError, TimeoutError, OSError) as exc:
        logger.warning("Failed to create async Redis client: %s", exc)
        yield None
    finally:
        if client:
            try:
                await client.aclose()
            except (RedisError, ConnectionError, OSError):
                # Best-effort cleanup - connection may already be closed
                pass


__all__ = ["async_redis_client"]
