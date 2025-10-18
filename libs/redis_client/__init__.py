"""
Redis Client Library for Trading Platform.

This library provides Redis integration for:
1. Feature caching (online feature store)
2. Event publishing/subscription (event bus)

Components:
    RedisClient: Connection manager with retry logic
    FeatureCache: Feature caching with TTL
    EventPublisher: Publish events to Redis channels
    EventSubscriber: Subscribe to Redis channels

Usage:
    from libs.redis_client import RedisClient, FeatureCache

    # Initialize Redis client
    redis_client = RedisClient(host="localhost", port=6379)

    # Use feature cache
    cache = FeatureCache(redis_client)
    cache.set("AAPL", "2025-01-17", {"feature_1": 0.5})
    features = cache.get("AAPL", "2025-01-17")

See Also:
    - docs/ADRs/0009-redis-integration.md for architecture decisions
    - docs/CONCEPTS/redis-patterns.md for usage patterns
"""

from .client import RedisClient, RedisConnectionError
from .feature_cache import FeatureCache
from .events import SignalEvent, OrderEvent, PositionEvent
from .event_publisher import EventPublisher

__all__ = [
    "RedisClient",
    "RedisConnectionError",
    "FeatureCache",
    "SignalEvent",
    "OrderEvent",
    "PositionEvent",
    "EventPublisher",
]

__version__ = "0.1.0"
