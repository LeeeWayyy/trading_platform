"""
Thread-safe in-memory cache for secret values with TTL expiration.

This module provides a reusable Cache class for caching secret values with
time-to-live (TTL) expiration. Designed for AC22 (trading safety) - allows
services to survive secrets backend downtime for up to 1 hour.

Architecture:
    - Thread-safe with threading.Lock for concurrent access
    - In-memory only (NO disk persistence for security)
    - Automatic TTL-based expiration (default: 1 hour)
    - Explicit invalidation support for security events

Security Properties:
    - AC22: 1-hour survival window during backend downtime
    - No disk persistence (secrets never written to filesystem)
    - Automatic expiration prevents stale credentials
    - Cache invalidation on 401 errors (invalid credentials)

Example Usage:
    >>> from datetime import timedelta
    >>> cache = SecretCache(ttl=timedelta(hours=1))
    >>>
    >>> # Store secret
    >>> cache.set("database/password", "secret123")
    >>>
    >>> # Retrieve (cache hit)
    >>> cache.get("database/password")
    'secret123'
    >>>
    >>> # Retrieve (cache miss after TTL)
    >>> time.sleep(3600)
    >>> cache.get("database/password")
    None
    >>>
    >>> # Invalidate cache on security event
    >>> cache.invalidate("database/password")

See Also:
    - docs/ADRs/0017-secrets-management.md - Cache architecture decisions
    - libs/secrets/manager.py - SecretManager interface
"""

import threading
from datetime import UTC, datetime, timedelta


class SecretCache:
    """
    Thread-safe in-memory cache for secret values with TTL expiration.

    This cache provides a time-to-live (TTL) based caching mechanism for secret
    values retrieved from backends (Vault, AWS Secrets Manager). Designed to support
    AC22 (trading safety) by allowing services to survive backend downtime for up to
    1 hour without halting trading operations.

    Security Properties:
        - Thread-safe: Uses threading.Lock for concurrent access protection
        - Memory-only: NO disk persistence (secrets never written to filesystem)
        - TTL expiration: Automatic expiration prevents stale credentials
        - Explicit invalidation: Security events (401 errors) can invalidate cache

    Attributes:
        _cache: Internal storage dict mapping secret names to (value, cached_at) tuples
        _ttl: Time-to-live duration for cached entries (default: 1 hour)
        _lock: Threading lock for concurrent access protection

    Thread Safety:
        All public methods are thread-safe and can be called from multiple threads
        concurrently without external synchronization.

    Examples:
        >>> # Create cache with default 1-hour TTL
        >>> cache = SecretCache()
        >>>
        >>> # Store and retrieve secret
        >>> cache.set("alpaca/api_key_id", "PKXXXXXXXX")
        >>> cache.get("alpaca/api_key_id")
        'PKXXXXXXXX'
        >>>
        >>> # Cache miss after TTL
        >>> cache.get("expired/secret")  # Returns None after 1 hour
        None
        >>>
        >>> # Explicit invalidation (e.g., on 401 error)
        >>> cache.invalidate("alpaca/api_key_id")
        >>> cache.get("alpaca/api_key_id")  # Returns None
        None
        >>>
        >>> # Clear all cached secrets
        >>> cache.clear()

    See Also:
        - VaultSecretManager: Uses inline caching with same pattern
        - AWSSecretsManager: Uses inline caching with same pattern
        - docs/ADRs/0017-secrets-management.md - AC22 trading safety requirements
    """

    def __init__(self, ttl: timedelta = timedelta(hours=1)) -> None:
        """
        Initialize SecretCache with configurable TTL.

        Args:
            ttl: Time-to-live duration for cached entries.
                 Default: 1 hour (AC22: trading safety requirement).

        Examples:
            >>> # Default 1-hour TTL
            >>> cache = SecretCache()
            >>>
            >>> # Custom 30-minute TTL (testing)
            >>> cache = SecretCache(ttl=timedelta(minutes=30))
        """
        self._cache: dict[str, tuple[str, datetime]] = {}
        self._ttl = ttl
        self._lock = threading.Lock()

    def get(self, name: str) -> str | None:
        """
        Retrieve cached secret value if not expired.

        Thread-safe retrieval with automatic TTL expiration checking.
        Expired entries are automatically removed from cache.

        Args:
            name: Secret name/path (e.g., "database/password", "alpaca/api_key_id")

        Returns:
            str: Cached secret value if found and not expired
            None: If secret not in cache or expired

        Examples:
            >>> cache = SecretCache()
            >>> cache.set("test/secret", "value123")
            >>> cache.get("test/secret")
            'value123'
            >>>
            >>> # Cache miss
            >>> cache.get("nonexistent/secret")
            None
        """
        with self._lock:
            if name not in self._cache:
                return None

            value, cached_at = self._cache[name]

            # Check TTL expiration
            if datetime.now(UTC) - cached_at >= self._ttl:
                # Expired - remove from cache
                del self._cache[name]
                return None

            return value

    def set(self, name: str, value: str) -> None:
        """
        Store secret value in cache with current timestamp.

        Thread-safe storage with automatic timestamp recording for TTL expiration.

        Args:
            name: Secret name/path (e.g., "database/password")
            value: Secret value to cache

        Examples:
            >>> cache = SecretCache()
            >>> cache.set("database/password", "secret123")
            >>> cache.get("database/password")
            'secret123'
        """
        with self._lock:
            self._cache[name] = (value, datetime.now(UTC))

    def invalidate(self, name: str) -> None:
        """
        Explicitly invalidate cached secret (e.g., on 401 error).

        Thread-safe removal of specific secret from cache. Use when credentials
        are detected as invalid (401 Unauthorized response from API).

        Args:
            name: Secret name/path to invalidate

        Examples:
            >>> cache = SecretCache()
            >>> cache.set("alpaca/api_key_id", "PKXXXXXXXX")
            >>> # Later: API returns 401 Unauthorized
            >>> cache.invalidate("alpaca/api_key_id")
            >>> cache.get("alpaca/api_key_id")  # Returns None
            None
        """
        with self._lock:
            if name in self._cache:
                del self._cache[name]

    def clear(self) -> None:
        """
        Clear all cached secrets.

        Thread-safe removal of all entries from cache. Use on service restart
        or security events requiring full cache invalidation.

        Examples:
            >>> cache = SecretCache()
            >>> cache.set("secret1", "value1")
            >>> cache.set("secret2", "value2")
            >>> cache.clear()
            >>> cache.get("secret1")  # Returns None
            None
        """
        with self._lock:
            self._cache.clear()

    def __len__(self) -> int:
        """
        Return number of cached secrets (for monitoring/debugging).

        Thread-safe count of cached entries (including expired entries that
        haven't been accessed yet).

        Returns:
            int: Number of cached secrets

        Examples:
            >>> cache = SecretCache()
            >>> len(cache)
            0
            >>> cache.set("secret1", "value1")
            >>> len(cache)
            1
        """
        with self._lock:
            return len(self._cache)
