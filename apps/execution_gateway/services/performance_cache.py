"""Performance dashboard caching service.

This module provides caching functions for the performance dashboard, enabling
efficient retrieval of daily P&L data with proper invalidation semantics.

Design Rationale:
    - User-scoped caching prevents cross-user data leakage
    - Strategy-scoped caching prevents stale data when RBAC assignments change
    - Per-date index enables targeted cache invalidation without global SCAN
    - SSCAN-based iteration avoids blocking Redis for large cache sets

Caching Strategy:
    - Cache keys include user_id and strategy hash for isolation
    - Each cached range registers itself in per-date index sets
    - When new trades arrive, invalidate all ranges containing that date
    - TTL-based expiry provides fallback cleanup

Usage:
    from apps.execution_gateway.services.performance_cache import (
        create_performance_cache_key,
        register_performance_cache,
        invalidate_performance_cache,
    )

    # Create cache key
    cache_key = create_performance_cache_key(
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
        strategies=("alpha_baseline",),
        user_id="user123",
    )

    # Register cached range
    register_performance_cache(cache_key, start_date, end_date)

    # Invalidate cache when new trades arrive
    invalidate_performance_cache(trade_date=date(2024, 1, 15))

See REFACTOR_EXECUTION_GATEWAY_TASK.md Phase 1 for design decisions.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import date, timedelta
from typing import TYPE_CHECKING

from redis.exceptions import RedisError

if TYPE_CHECKING:
    from libs.core.redis_client import RedisClient

logger = logging.getLogger(__name__)


def create_performance_cache_key(
    start_date: date, end_date: date, strategies: tuple[str, ...], user_id: str | None
) -> str:
    """Create cache key for performance range scoped by strategies and user.

    Per T6.2 plan iteration 10, the cache must be user-scoped AND strategy-scoped
    to prevent cross-user leakage and stale data when RBAC assignments change.

    Args:
        start_date: Start of the date range (inclusive)
        end_date: End of the date range (inclusive)
        strategies: Tuple of strategy IDs to scope the cache
        user_id: User ID for user-scoped caching (None for anonymous)

    Returns:
        Cache key string in format: "performance:daily:{user}:{start}:{end}:{strat_hash}"

    Notes:
        - Strategies are sorted and hashed to create a deterministic 8-char token
        - Empty strategies tuple results in "none" token
        - User ID is included to prevent cross-user data leakage
        - Hash ensures key length remains bounded even with many strategies

    Example:
        >>> create_performance_cache_key(
        ...     start_date=date(2024, 1, 1),
        ...     end_date=date(2024, 1, 31),
        ...     strategies=("alpha_baseline", "momentum"),
        ...     user_id="user123",
        ... )
        'performance:daily:user123:2024-01-01:2024-01-31:a3f7b2e9'
    """
    strat_token = "none" if not strategies else ",".join(sorted(strategies))
    strat_hash = hashlib.md5(strat_token.encode()).hexdigest()[:8]
    user_token = user_id or "anon"
    return f"performance:daily:{user_token}:{start_date}:{end_date}:{strat_hash}"


def create_performance_cache_index_key(trade_date: date) -> str:
    """Create index key to track which cache entries include a given trade date.

    The index enables targeted cache invalidation without global SCAN operations.
    When new trades arrive, we can quickly find and invalidate all cached ranges
    that include the affected date.

    Args:
        trade_date: Trading date to create index key for

    Returns:
        Index key string in format: "performance:daily:index:{date}"

    Notes:
        - Each date has its own Redis SET containing affected cache keys
        - Index keys expire with same TTL as cache entries
        - Used by invalidate_performance_cache for targeted invalidation

    Example:
        >>> create_performance_cache_index_key(date(2024, 1, 15))
        'performance:daily:index:2024-01-15'
    """
    return f"performance:daily:index:{trade_date}"


def register_performance_cache(
    redis_client: RedisClient | None,
    cache_key: str,
    start_date: date,
    end_date: date,
    ttl_seconds: int,
) -> None:
    """Track cached ranges by each included trade date for targeted invalidation.

    This function builds a bidirectional index between dates and cache keys.
    For each date in the cached range, we add the cache key to that date's index set.
    This enables efficient invalidation: when new trades arrive on date D, we can
    quickly find and delete all cached ranges that include D.

    Args:
        redis_client: Redis client instance (None skips registration)
        cache_key: Cache key to register
        start_date: Start of the cached date range (inclusive)
        end_date: End of the cached date range (inclusive)
        ttl_seconds: TTL for index entries (should match cache TTL)

    Notes:
        - Uses Redis pipeline for atomic multi-date registration
        - Each index set gets same TTL as cache entry
        - Failures are logged but don't raise (degraded mode: no invalidation)
        - If Redis is unavailable, caching still works but without invalidation

    Example:
        >>> register_performance_cache(
        ...     redis_client,
        ...     "performance:daily:user123:2024-01-01:2024-01-31:a3f7b2e9",
        ...     date(2024, 1, 1),
        ...     date(2024, 1, 31),
        ...     300,
        ... )
        # Creates index entries for all dates 2024-01-01 through 2024-01-31
    """
    if not redis_client:
        return

    try:
        pipe = redis_client.pipeline()
        current = start_date
        while current <= end_date:
            index_key = create_performance_cache_index_key(current)
            pipe.sadd(index_key, cache_key)
            pipe.expire(index_key, ttl_seconds)
            current += timedelta(days=1)
        pipe.execute()
    except RedisError as e:
        logger.warning(
            "Performance cache index registration failed - Redis error",
            extra={"error": str(e), "error_type": type(e).__name__},
        )
    except (AttributeError, TypeError) as e:
        logger.warning(
            "Performance cache index registration failed - invalid data",
            extra={"error": str(e), "error_type": type(e).__name__},
        )


def invalidate_performance_cache(
    redis_client: RedisClient | None,
    trade_date: date | None = None,
) -> None:
    """Invalidate cached performance ranges that include the given trade_date.

    Falls back to today's date when trade_date is not provided. This avoids a
    global SCAN across all cache keys by leveraging per-date index sets that are
    maintained when caching responses.

    Uses SSCAN instead of SMEMBERS to avoid blocking the Redis event loop for
    large sets. Cache keys and index key are deleted atomically in a single
    call to prevent stale index entries if the process fails mid-operation.

    Args:
        redis_client: Redis client instance (None skips invalidation)
        trade_date: Date to invalidate (defaults to today)

    Notes:
        - Invalidates ALL cached ranges that include the target date
        - Processes deletions in batches (100 keys) to maintain O(1) memory
        - Atomic deletion of batch + index key prevents stale index entries
        - Failures are logged but don't raise (graceful degradation)

    Design Decision:
        Why SSCAN instead of SMEMBERS? SSCAN iterates incrementally without
        blocking Redis, critical for large index sets. The trade-off is slightly
        higher latency, but this is acceptable for cache invalidation (not hot path).

    Example:
        >>> # Invalidate all ranges including today
        >>> invalidate_performance_cache(redis_client)
        >>>
        >>> # Invalidate all ranges including specific date
        >>> invalidate_performance_cache(redis_client, date(2024, 1, 15))
    """
    if not redis_client:
        return

    target_date = trade_date or date.today()
    index_key = create_performance_cache_index_key(target_date)

    try:
        # Stream deletions in batches to maintain O(1) memory for large index sets
        batch: list[str] = []
        batch_size = 100

        for key in redis_client.sscan_iter(index_key):
            batch.append(key)
            if len(batch) >= batch_size:
                redis_client.delete(*batch)
                batch = []

        # Delete remaining batch + index key
        if batch:
            redis_client.delete(*batch, index_key)
        else:
            # If batch is empty, just delete the index key.
            # This covers cases where the set was empty or its size was a multiple of batch_size.
            redis_client.delete(index_key)
    except RedisError as e:
        logger.warning(
            "Performance cache invalidation failed - Redis error",
            extra={"error": str(e), "error_type": type(e).__name__},
        )
    except (AttributeError, TypeError) as e:
        logger.warning(
            "Performance cache invalidation failed - invalid data",
            extra={"error": str(e), "error_type": type(e).__name__},
        )
