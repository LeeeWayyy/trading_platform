"""
from __future__ import annotations

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

import builtins
import logging
from typing import Any, cast

import redis
from redis.connection import ConnectionPool
from redis.exceptions import ConnectionError, RedisError, TimeoutError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

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
        password: str | None = None,
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
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
    )
    def get(self, key: str) -> str | None:
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
            # Redis library returns Awaitable[Any] | Any, cast to expected type
            result = self._client.get(key)
            return cast(str | None, result)
        except RedisError as e:
            logger.error(f"Redis GET failed for key '{key}': {e}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
    )
    def mget(self, keys: list[str]) -> list[str | None]:
        """
        Get multiple values from Redis in a single round-trip.

        This is significantly more efficient than multiple individual GET calls,
        especially when fetching many keys (e.g., prices for multiple symbols).

        Args:
            keys: List of Redis keys to retrieve

        Returns:
            List of values in the same order as keys. Missing keys return None.

        Raises:
            RedisError: If operation fails after retries

        Example:
            >>> keys = ["price:AAPL", "price:MSFT", "price:GOOGL"]
            >>> values = client.mget(keys)
            >>> # values = ['{"mid": 150.25, ...}', '{"mid": 380.50, ...}', None]

        Performance:
            - 10 symbols: 1 Redis call vs 10 individual calls
            - Reduces network round-trips from O(N) to O(1)
            - Typical speedup: 5-10x for 10+ keys

        Notes:
            - Returns None for missing keys (not an error)
            - Order of values matches order of input keys
            - Empty list returns empty list (not an error)
        """
        if not keys:
            return []

        try:
            result = self._client.mget(keys)
            return cast(list[str | None], result)
        except RedisError as e:
            logger.error(f"Redis MGET failed for {len(keys)} keys: {e}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
    )
    def set(self, key: str, value: str, ttl: int | None = None) -> None:
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
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
    )
    def delete(self, *keys: str) -> int:
        """
        Delete one or more keys from Redis with retry logic.

        Args:
            *keys: Redis keys to delete

        Returns:
            Number of keys deleted

        Raises:
            RedisError: If operation fails after retries

        Example:
            >>> deleted = client.delete("features:AAPL:2025-01-17", "features:MSFT:2025-01-17")
            >>> assert deleted >= 1
        """
        if not keys:
            return 0
        try:
            result = self._client.delete(*keys)
            return cast(int, result)
        except RedisError as e:
            logger.error(f"Redis DELETE failed for keys {keys}: {e}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
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
            result = self._client.publish(channel, message)
            return cast(int, result)
        except RedisError as e:
            logger.error(f"Redis PUBLISH failed for channel '{channel}': {e}")
            raise

    def pubsub(self) -> Any:
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
        return self._client.pubsub()  # type: ignore[no-untyped-call]

    def pipeline(self, transaction: bool = True) -> Any:
        """
        Create a pipeline for atomic operations with WATCH/MULTI/EXEC.

        Pipelines allow batching multiple commands and executing them atomically.
        Use with context manager for automatic cleanup.

        Args:
            transaction: If True, wrap commands in MULTI/EXEC (default: True)

        Returns:
            Redis Pipeline object

        Example:
            >>> with client.pipeline() as pipe:
            ...     pipe.watch("my_key")  # Watch for changes
            ...     value = pipe.get("my_key")
            ...     pipe.multi()  # Start transaction
            ...     pipe.set("my_key", "new_value")
            ...     pipe.execute()  # Execute atomically

        Notes:
            - Use watch() to detect concurrent modifications
            - Call multi() to start the transaction block
            - Call execute() to run all queued commands atomically
            - WatchError is raised if watched key was modified by another client
        """
        return self._client.pipeline(transaction=transaction)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
    )
    def sadd(self, key: str, *members: str) -> int:
        """Add one or more members to a set."""
        try:
            result = self._client.sadd(key, *members)
            return cast(int, result)
        except RedisError as e:
            logger.error(f"Redis SADD failed for key '{key}': {e}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
    )
    def smembers(self, key: str) -> builtins.set[str]:
        """Return all members of a set.

        Note: For large sets in production, prefer sscan_iter() to avoid blocking.
        """
        try:
            result = self._client.smembers(key)
            return cast(builtins.set[str], result)
        except RedisError as e:
            logger.error(f"Redis SMEMBERS failed for key '{key}': {e}")
            raise

    def sscan_iter(self, key: str, count: int = 100) -> builtins.set[str]:
        """Iterate over set members using SSCAN (non-blocking).

        Unlike SMEMBERS which blocks the Redis event loop for the entire set,
        SSCAN iterates in batches, yielding to other clients between iterations.
        Use this for production cache invalidation to avoid blocking.

        Args:
            key: Redis set key
            count: Hint for how many items to return per iteration (default 100)

        Returns:
            Set of all members (collected from all SSCAN iterations)
        """
        try:
            # sscan_iter returns a generator, collect all members
            members: builtins.set[str] = set()
            for member in self._client.sscan_iter(key, count=count):
                if isinstance(member, bytes):
                    members.add(member.decode("utf-8"))
                else:
                    members.add(str(member))
            return members
        except RedisError as e:
            logger.error(f"Redis SSCAN failed for key '{key}': {e}")
            raise

    def zadd(self, key: str, mapping: dict[str, float]) -> int:
        """
        Add members to a sorted set with scores.

        Args:
            key: Redis sorted set key
            mapping: Dictionary of {member: score} pairs

        Returns:
            Number of new members added

        Example:
            >>> client.zadd("my_zset", {"member1": 1.0, "member2": 2.0})
            2
        """
        try:
            result = self._client.zadd(key, mapping)
            return cast(int, result)
        except RedisError as e:
            logger.error(f"Redis ZADD failed for key '{key}': {e}")
            raise

    def zcard(self, key: str) -> int:
        """
        Get the number of members in a sorted set.

        Args:
            key: Redis sorted set key

        Returns:
            Number of members in the sorted set

        Example:
            >>> client.zcard("my_zset")
            5
        """
        try:
            result = self._client.zcard(key)
            return cast(int, result)
        except RedisError as e:
            logger.error(f"Redis ZCARD failed for key '{key}': {e}")
            raise

    def zremrangebyrank(self, key: str, start: int, stop: int) -> int:
        """
        Remove members from a sorted set by rank (index).

        Args:
            key: Redis sorted set key
            start: Start rank (0-based, inclusive)
            stop: Stop rank (0-based, inclusive)

        Returns:
            Number of members removed

        Example:
            >>> # Remove first 5 members (ranks 0-4)
            >>> client.zremrangebyrank("my_zset", 0, 4)
            5
        """
        try:
            result = self._client.zremrangebyrank(key, start, stop)
            return cast(int, result)
        except RedisError as e:
            logger.error(f"Redis ZREMRANGEBYRANK failed for key '{key}': {e}")
            raise

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

    def get_info(self) -> dict[str, Any]:
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
            result = self._client.info()
            return cast(dict[str, Any], result)
        except RedisError as e:
            logger.error(f"Redis INFO failed: {e}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
    )
    def rpush(self, key: str, *values: str) -> int:
        """
        Append one or more values to a list with retry logic.

        Args:
            key: Redis key for the list
            *values: Values to append

        Returns:
            Length of the list after the push operation

        Raises:
            RedisError: If operation fails after retries

        Example:
            >>> length = client.rpush("mylist", "value1", "value2")
            >>> print(f"List length: {length}")
        """
        try:
            result = self._client.rpush(key, *values)
            return cast(int, result)
        except RedisError as e:
            logger.error(f"Redis RPUSH failed for key {key}: {e}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
    )
    def ltrim(self, key: str, start: int, stop: int) -> bool:
        """
        Trim a list to the specified range with retry logic.

        Args:
            key: Redis key for the list
            start: Start index (0-based)
            stop: Stop index (inclusive, -1 for end)

        Returns:
            True if operation succeeded

        Raises:
            RedisError: If operation fails after retries

        Example:
            >>> # Keep last 100 items
            >>> client.ltrim("mylist", -100, -1)
        """
        try:
            self._client.ltrim(key, start, stop)
            return True
        except RedisError as e:
            logger.error(f"Redis LTRIM failed for key {key}: {e}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
    )
    def lrange(self, key: str, start: int, stop: int) -> list[bytes]:
        """
        Get a range of elements from a list with retry logic.

        Args:
            key: Redis key for the list
            start: Start index (0-based)
            stop: Stop index (inclusive, -1 for end)

        Returns:
            List of elements (as bytes)

        Raises:
            RedisError: If operation fails after retries

        Example:
            >>> # Get last 10 items
            >>> items = client.lrange("mylist", -10, -1)
            >>> for item in items:
            ...     print(item.decode('utf-8'))
        """
        try:
            result = self._client.lrange(key, start, stop)
            return cast(list[bytes], result)
        except RedisError as e:
            logger.error(f"Redis LRANGE failed for key {key}: {e}")
            raise

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((ConnectionError, TimeoutError)),
    )
    def eval(self, script: str, numkeys: int, *keys_and_args: str) -> Any:
        """
        Execute a Lua script atomically with retry logic.

        Redis guarantees Lua scripts run atomically, making them ideal for
        complex read-modify-write operations that must be free of race conditions.

        Args:
            script: Lua script to execute
            numkeys: Number of keys (vs args) in keys_and_args
            *keys_and_args: Keys followed by args

        Returns:
            Script result (type depends on script)

        Raises:
            RedisError: If operation fails after retries

        Example:
            >>> # Atomic compare-and-set
            >>> script = '''
            ... if redis.call("GET", KEYS[1]) == ARGV[1] then
            ...     redis.call("SET", KEYS[1], ARGV[2])
            ...     return 1
            ... else
            ...     return 0
            ... end
            ... '''
            >>> result = client.eval(script, 1, "mykey", "old_value", "new_value")

        Performance:
            - Scripts are sent to Redis and executed server-side
            - Redis caches compiled scripts for efficiency
            - Atomic execution prevents race conditions

        Notes:
            - All keys must be passed before args (per Redis convention)
            - numkeys tells Redis how many keys vs args
        """
        try:
            result = self._client.eval(script, numkeys, *keys_and_args)
            return result
        except RedisError as e:
            logger.error(f"Redis EVAL failed: {e}")
            raise

    def close(self) -> None:
        """
        Close connection pool and release resources.

        Should be called when shutting down the application.

        Example:
            >>> client.close()
        """
        logger.info("Closing Redis connection pool")
        self.pool.disconnect()

    def __enter__(self) -> "RedisClient":
        """Context manager entry."""
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """Context manager exit."""
        self.close()

    def __repr__(self) -> str:
        """String representation."""
        return f"RedisClient(host={self.host}, port={self.port}, db={self.db})"
