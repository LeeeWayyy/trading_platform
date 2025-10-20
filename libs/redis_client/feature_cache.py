"""
Feature cache for Alpha158 features using Redis.

This module provides caching for generated trading features to reduce
computation time and improve signal generation performance.

Key Format:
    features:{symbol}:{date} -> JSON serialized feature dictionary

Example:
    >>> from libs.redis_client import RedisClient, FeatureCache
    >>> client = RedisClient()
    >>> cache = FeatureCache(client, ttl=3600)
    >>>
    >>> # Cache features
    >>> features = {"feature_1": 0.5, "feature_2": 0.3}
    >>> cache.set("AAPL", "2025-01-17", features)
    >>>
    >>> # Retrieve from cache
    >>> cached = cache.get("AAPL", "2025-01-17")
    >>> assert cached == features

See Also:
    - docs/ADRs/0009-redis-integration.md for architecture
    - apps/signal_service/signal_generator.py for integration
"""

import json
import logging
from typing import Any

from redis.exceptions import RedisError

from .client import RedisClient

logger = logging.getLogger(__name__)


class FeatureCache:
    """
    Redis-backed cache for Alpha158 features.

    Features are immutable for a given (symbol, date) pair, making them
    ideal for caching. TTL ensures stale data doesn't persist if data
    corrections occur.

    Attributes:
        redis: Redis client instance
        ttl: Time-to-live for cached features in seconds
        prefix: Key prefix for namespacing

    Performance:
        - Cache HIT: ~5ms (Redis GET + JSON decode)
        - Cache MISS: ~50ms (feature generation + Redis SET)
        - Expected hit rate: 70-80% for repeated symbols

    Example:
        >>> cache = FeatureCache(redis_client, ttl=3600)
        >>>
        >>> # Try cache first
        >>> features = cache.get("AAPL", "2025-01-17")
        >>> if not features:
        ...     # Cache miss - generate features
        ...     features = generate_features("AAPL", "2025-01-17")
        ...     cache.set("AAPL", "2025-01-17", features)
    """

    def __init__(
        self,
        redis_client: RedisClient,
        ttl: int = 3600,
        prefix: str = "features"
    ):
        """
        Initialize feature cache.

        Args:
            redis_client: Initialized Redis client
            ttl: Time-to-live in seconds (default: 3600 = 1 hour)
            prefix: Key prefix for namespacing (default: "features")

        Notes:
            - TTL of 1 hour balances freshness vs cache hits
            - Historical features are immutable, but TTL handles edge cases
            - Prefix allows multiple cache types in same Redis instance
        """
        self.redis = redis_client
        self.ttl = ttl
        self.prefix = prefix

        logger.info(f"Feature cache initialized (ttl={ttl}s, prefix={prefix})")

    def _make_key(self, symbol: str, date: str) -> str:
        """
        Generate Redis key for (symbol, date) pair.

        Args:
            symbol: Stock symbol (e.g., "AAPL")
            date: Date string (e.g., "2025-01-17")

        Returns:
            Redis key (e.g., "features:AAPL:2025-01-17")

        Example:
            >>> key = cache._make_key("AAPL", "2025-01-17")
            >>> assert key == "features:AAPL:2025-01-17"
        """
        return f"{self.prefix}:{symbol}:{date}"

    def get(self, symbol: str, date: str) -> dict[str, Any] | None:
        """
        Retrieve cached features for (symbol, date).

        Args:
            symbol: Stock symbol (e.g., "AAPL")
            date: Date string (e.g., "2025-01-17")

        Returns:
            Feature dictionary if cached, None otherwise

        Example:
            >>> features = cache.get("AAPL", "2025-01-17")
            >>> if features:
            ...     print("Cache HIT")
            ... else:
            ...     print("Cache MISS")

        Notes:
            - Returns None if key doesn't exist or expired
            - Logs cache hits/misses at DEBUG level
            - Handles JSON decode errors gracefully
        """
        key = self._make_key(symbol, date)

        try:
            data = self.redis.get(key)

            if data is None:
                logger.debug(f"Cache MISS: {symbol} on {date}")
                return None

            # Deserialize JSON
            features_data = json.loads(data)
            # Cast from Any to expected dict type
            features: dict[str, Any] = features_data
            logger.debug(f"Cache HIT: {symbol} on {date} ({len(features)} features)")
            return features

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in cache for {symbol} on {date}: {e}")
            # Invalidate corrupted data
            self.invalidate(symbol, date)
            return None

        except RedisError as e:
            logger.error(f"Redis error retrieving features for {symbol} on {date}: {e}")
            # Return None on error (graceful degradation)
            return None

    def set(self, symbol: str, date: str, features: dict[str, Any]) -> bool:
        """
        Cache features for (symbol, date) with TTL.

        Args:
            symbol: Stock symbol (e.g., "AAPL")
            date: Date string (e.g., "2025-01-17")
            features: Feature dictionary to cache

        Returns:
            True if cached successfully, False otherwise

        Example:
            >>> features = {"feature_1": 0.5, "feature_2": 0.3}
            >>> success = cache.set("AAPL", "2025-01-17", features)
            >>> assert success

        Notes:
            - Features are serialized to JSON
            - TTL ensures data doesn't persist indefinitely
            - Logs errors but doesn't raise (graceful degradation)
        """
        key = self._make_key(symbol, date)

        try:
            # Serialize to JSON
            data = json.dumps(features)

            # Set with TTL
            self.redis.set(key, data, ttl=self.ttl)

            logger.debug(
                f"Cached features: {symbol} on {date} "
                f"({len(features)} features, ttl={self.ttl}s)"
            )
            return True

        except (TypeError, ValueError) as e:
            logger.error(f"Cannot serialize features for {symbol} on {date}: {e}")
            return False

        except RedisError as e:
            logger.error(f"Redis error caching features for {symbol} on {date}: {e}")
            return False

    def invalidate(self, symbol: str, date: str) -> bool:
        """
        Invalidate cached features for (symbol, date).

        Used when data corrections occur or cache is corrupted.

        Args:
            symbol: Stock symbol (e.g., "AAPL")
            date: Date string (e.g., "2025-01-17")

        Returns:
            True if key was deleted, False if didn't exist

        Example:
            >>> cache.invalidate("AAPL", "2025-01-17")
            >>> assert cache.get("AAPL", "2025-01-17") is None

        Notes:
            - Safe to call even if key doesn't exist
            - Logs invalidation at INFO level
        """
        key = self._make_key(symbol, date)

        try:
            deleted = self.redis.delete(key)
            if deleted:
                logger.info(f"Invalidated cache: {symbol} on {date}")
            return bool(deleted)

        except RedisError as e:
            logger.error(f"Redis error invalidating {symbol} on {date}: {e}")
            return False

    def get_stats(self) -> dict[str, Any]:
        """
        Get cache statistics (if Redis INFO command available).

        Returns:
            Dictionary with cache stats

        Example:
            >>> stats = cache.get_stats()
            >>> print(f"Keyspace hits: {stats.get('keyspace_hits', 0)}")
            >>> print(f"Keyspace misses: {stats.get('keyspace_misses', 0)}")

        Notes:
            - Requires Redis INFO command access
            - Hit rate = hits / (hits + misses)
        """
        try:
            info = self.redis.get_info()
            return {
                "keyspace_hits": info.get("keyspace_hits", 0),
                "keyspace_misses": info.get("keyspace_misses", 0),
                "used_memory": info.get("used_memory_human", "unknown"),
                "connected_clients": info.get("connected_clients", 0),
            }
        except RedisError as e:
            logger.error(f"Cannot retrieve cache stats: {e}")
            return {}

    def __repr__(self) -> str:
        """String representation."""
        return f"FeatureCache(ttl={self.ttl}s, prefix={self.prefix})"
