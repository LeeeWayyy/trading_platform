"""
Redis connection manager with retry logic and health checks.

This module provides a thread-safe Redis client with:
- Connection pooling for performance
- Retry logic for transient failures
- Health checks for monitoring
- Graceful degradation support

Example:
    >>> from libs.redis_client import RedisClient
    >>> client = RedisClient(host="localhost", port=6379)
    >>> if client.health_check():
    ...     client.set("key", "value")
    ...     value = client.get("key")
    >>> client.close()

See Also:
    - docs/ADRs/0009-redis-integration.md for design rationale
"""

import logging
from typing import Optional, Dict, Any
import redis
from redis.connection import ConnectionPool
from redis.exceptions import ConnectionError, TimeoutError, RedisError
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type
)

logger = logging.getLogger(__name__)


class RedisConnectionError(Exception):
    """Raised when Redis connection fails."""
    pass


class RedisClient:
    """
    Redis connection manager with retry logic.

    Handles connection pooling, health checks, and graceful failures.

    Attributes:
        host: Redis server hostname
        port: Redis server port
        db: Redis database number (0-15)
        pool: Connection pool (thread-safe)

    Example:
        >>> client = RedisClient(host="localhost", port=6379)
        >>> client.set("features:AAPL:2025-01-17", '{"f1": 0.5}', ttl=3600)
        >>> value = client.get("features:AAPL:2025-01-17")
        >>> client.delete("features:AAPL:2025-01-17")
        >>> client.close()

    Notes:
        - Connection pool is thread-safe
        - Retry logic handles transient network errors
        - All methods log errors for debugging
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        db: int = 0,
        password: Optional[str] = None,
        decode_responses: bool = True,
        max_connections: int = 10,
        socket_connect_timeout: int = 5,
        socket_timeout: int = 5,
    ):
        """
        Initialize Redis client with connection pooling.

        Args:
            host: Redis server hostname (default: localhost)
            port: Redis server port (default: 6379)
            db: Redis database number (default: 0)
            password: Redis password (default: None)
            decode_responses: Auto-decode responses to str (default: True)
            max_connections: Max connections in pool (default: 10)
            socket_connect_timeout: Connection timeout in seconds (default: 5)
            socket_timeout: Socket operation timeout in seconds (default: 5)

        Raises:
            RedisConnectionError: If initial connection fails
        """
        self.host = host
        self.port = port
        self.db = db

        logger.info(
            f"Initializing Redis client: {host}:{port} (db={db}, "
            f"max_connections={max_connections})"
        )

        try:
            # Create connection pool (thread-safe)
            self.pool = ConnectionPool(
                host=host,
                port=port,
                db=db,
                password=password,
                decode_responses=decode_responses,
                max_connections=max_connections,
                socket_connect_timeout=socket_connect_timeout,
                socket_timeout=socket_timeout,
            )

            # Create Redis client from pool
            self._client = redis.Redis(connection_pool=self.pool)

            # Test connection
            self._client.ping()
            logger.info("Redis connection established successfully")

        except (ConnectionError, TimeoutError) as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise RedisConnectionError(f"Cannot connect to Redis at {host}:{port}") from e

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((ConnectionError, TimeoutError))
    )
    def get(self, key: str) -> Optional[str]:
        """
        Get value from Redis with retry logic.

        Args:
            key: Redis key to retrieve

        Returns:
            Value as string, or None if key doesn't exist

        Raises:
            RedisError: If operation fails after retries

        Example:
            >>> value = client.get("features:AAPL:2025-01-17")
            >>> if value:
            ...     features = json.loads(value)
        """
        try:
            return self._client.get(key)
        except RedisError as e:
            logger.error(f"Redis GET failed for key '{key}': {e}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((ConnectionError, TimeoutError))
    )
    def set(self, key: str, value: str, ttl: Optional[int] = None) -> None:
        """
        Set value in Redis with optional TTL and retry logic.

        Args:
            key: Redis key to set
            value: Value to store (string)
            ttl: Time-to-live in seconds (optional)

        Raises:
            RedisError: If operation fails after retries

        Example:
            >>> client.set("features:AAPL:2025-01-17", '{"f1": 0.5}', ttl=3600)
        """
        try:
            if ttl:
                self._client.setex(key, ttl, value)
            else:
                self._client.set(key, value)
        except RedisError as e:
            logger.error(f"Redis SET failed for key '{key}': {e}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((ConnectionError, TimeoutError))
    )
    def delete(self, key: str) -> int:
        """
        Delete key from Redis with retry logic.

        Args:
            key: Redis key to delete

        Returns:
            Number of keys deleted (0 or 1)

        Raises:
            RedisError: If operation fails after retries

        Example:
            >>> deleted = client.delete("features:AAPL:2025-01-17")
            >>> assert deleted == 1
        """
        try:
            return self._client.delete(key)
        except RedisError as e:
            logger.error(f"Redis DELETE failed for key '{key}': {e}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((ConnectionError, TimeoutError))
    )
    def publish(self, channel: str, message: str) -> int:
        """
        Publish message to Redis pub/sub channel with retry logic.

        Args:
            channel: Channel name
            message: Message to publish (string, typically JSON)

        Returns:
            Number of subscribers that received the message

        Raises:
            RedisError: If operation fails after retries

        Example:
            >>> client.publish("signals.generated", '{"symbol": "AAPL"}')
        """
        try:
            return self._client.publish(channel, message)
        except RedisError as e:
            logger.error(f"Redis PUBLISH failed for channel '{channel}': {e}")
            raise

    def pubsub(self):
        """
        Create a pub/sub object for subscribing to channels.

        Returns:
            Redis PubSub object

        Example:
            >>> pubsub = client.pubsub()
            >>> pubsub.subscribe('signals.generated')
            >>> for message in pubsub.listen():
            ...     if message['type'] == 'message':
            ...         print(message['data'])
        """
        return self._client.pubsub()

    def health_check(self) -> bool:
        """
        Check Redis connectivity.

        Returns:
            True if Redis is reachable, False otherwise

        Example:
            >>> if client.health_check():
            ...     print("Redis is healthy")
            ... else:
            ...     print("Redis is down")
        """
        try:
            self._client.ping()
            return True
        except RedisError as e:
            logger.warning(f"Redis health check failed: {e}")
            return False

    def get_info(self) -> Dict[str, Any]:
        """
        Get Redis server information.

        Returns:
            Dictionary with Redis server info

        Raises:
            RedisError: If operation fails

        Example:
            >>> info = client.get_info()
            >>> print(f"Used memory: {info.get('used_memory_human')}")
        """
        try:
            return self._client.info()
        except RedisError as e:
            logger.error(f"Redis INFO failed: {e}")
            raise

    def close(self):
        """
        Close connection pool and release resources.

        Should be called when shutting down the application.

        Example:
            >>> client.close()
        """
        logger.info("Closing Redis connection pool")
        self.pool.disconnect()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()

    def __repr__(self):
        """String representation."""
        return f"RedisClient(host={self.host}, port={self.port}, db={self.db})"
