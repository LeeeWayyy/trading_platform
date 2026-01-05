"""Async database pool accessor for NiceGUI web console.

This module provides a centralized way to access the async database pool
without circular imports. The pool is initialized by main.py on startup
and accessed by page modules via get_db_pool().

⚠️ CRITICAL: This module exists to prevent circular imports.
Pages MUST import from here, NOT from main.py.

Usage:
    from apps.web_console_ng.core.database import get_db_pool

    async_pool = get_db_pool()
    if async_pool is None:
        # DATABASE_URL not configured
        ui.notify("Database not configured", type="negative")
        return
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)

# Module-level pool reference, set by init_db_pool()
_db_pool: AsyncConnectionPool | None = None


def init_db_pool() -> AsyncConnectionPool | None:
    """Initialize async DB pool (call from main.py startup).

    Returns:
        AsyncConnectionPool if DATABASE_URL is set, None otherwise.

    Note:
        The pool is created with open=False. Call await pool.open()
        during app startup to establish connections.
    """
    global _db_pool

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        logger.info("DATABASE_URL not set, async DB pool disabled")
        return None

    try:
        from psycopg_pool import AsyncConnectionPool
    except ImportError:
        logger.warning("psycopg_pool not installed, async DB pool disabled")
        return None

    min_size = int(os.getenv("DB_POOL_MIN_SIZE", "1"))
    max_size = int(os.getenv("DB_POOL_MAX_SIZE", "5"))
    timeout = float(os.getenv("DB_POOL_TIMEOUT", "10.0"))

    _db_pool = AsyncConnectionPool(
        dsn,
        min_size=min_size,
        max_size=max_size,
        timeout=timeout,
        open=False,
    )
    logger.info("Async DB pool initialized (min=%d, max=%d)", min_size, max_size)
    return _db_pool


def get_db_pool() -> AsyncConnectionPool | None:
    """Get async DB pool (returns None if not configured).

    Returns:
        AsyncConnectionPool if initialized, None otherwise.

    Example:
        async_pool = get_db_pool()
        if async_pool is None:
            ui.notify("Database not configured", type="negative")
            return

        async with async_pool.connection() as conn:
            result = await conn.execute("SELECT 1")
    """
    return _db_pool


def set_db_pool(pool: AsyncConnectionPool | None) -> None:
    """Set the DB pool reference (for migration from main.py).

    This allows main.py to set the pool after initialization,
    supporting gradual migration from the old pattern.

    Args:
        pool: The AsyncConnectionPool instance or None.
    """
    global _db_pool
    _db_pool = pool


async def close_db_pool() -> None:
    """Close async DB pool on shutdown."""
    global _db_pool
    if _db_pool is not None:
        await _db_pool.close()
        _db_pool = None
        logger.info("Async DB pool closed")
