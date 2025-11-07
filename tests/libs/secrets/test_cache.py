"""
Tests for libs/secrets/cache.py - Thread-Safe Secret Cache with TTL.

Test Coverage:
    - Basic get/set operations
    - TTL expiration behavior
    - Thread safety (concurrent access)
    - Explicit invalidation
    - Cache clearing
    - Custom TTL configuration

Test Organization:
    - TestSecretCacheBasicOperations: Get, set, cache hits/misses
    - TestSecretCacheTTLExpiration: TTL expiration logic
    - TestSecretCacheThreadSafety: Concurrent access safety
    - TestSecretCacheInvalidation: Explicit cache invalidation
"""

import time
from datetime import timedelta
from threading import Thread
from typing import Callable

import pytest

from libs.secrets.cache import SecretCache


class TestSecretCacheBasicOperations:
    """Test basic cache operations (get, set, cache hits/misses)."""

    @pytest.mark.unit()
    def test_get_nonexistent_secret_returns_none(self) -> None:
        """
        Get on nonexistent key returns None.

        Verifies that cache miss (key not in cache) returns None rather than
        raising an exception (consistent with dict.get() behavior).
        """
        cache = SecretCache()
        result = cache.get("nonexistent/secret")
        assert result is None

    @pytest.mark.unit()
    def test_set_and_get_secret(self) -> None:
        """
        Set and get retrieve cached value.

        Verifies that cache.set() stores value and cache.get() retrieves it
        successfully (basic cache hit scenario).
        """
        cache = SecretCache()
        cache.set("test/secret", "secret_value_123")

        result = cache.get("test/secret")
        assert result == "secret_value_123"

    @pytest.mark.unit()
    def test_set_overwrites_existing_value(self) -> None:
        """
        Set overwrites existing cached value.

        Verifies that calling cache.set() with same key replaces the previous
        value and resets the TTL timer.
        """
        cache = SecretCache()
        cache.set("test/secret", "old_value")
        cache.set("test/secret", "new_value")

        result = cache.get("test/secret")
        assert result == "new_value"

    @pytest.mark.unit()
    def test_cache_stores_multiple_secrets(self) -> None:
        """
        Cache stores multiple independent secrets.

        Verifies that cache can store multiple key-value pairs independently
        without interference (no key collision).
        """
        cache = SecretCache()
        cache.set("database/password", "db_pass_123")
        cache.set("alpaca/api_key_id", "PK_TEST_KEY")
        cache.set("redis/password", "redis_pass_456")

        assert cache.get("database/password") == "db_pass_123"
        assert cache.get("alpaca/api_key_id") == "PK_TEST_KEY"
        assert cache.get("redis/password") == "redis_pass_456"

    @pytest.mark.unit()
    def test_len_returns_cache_size(self) -> None:
        """
        __len__() returns number of cached secrets.

        Verifies that len(cache) returns count of cached entries (useful for
        monitoring and debugging).
        """
        cache = SecretCache()
        assert len(cache) == 0

        cache.set("secret1", "value1")
        assert len(cache) == 1

        cache.set("secret2", "value2")
        assert len(cache) == 2


class TestSecretCacheTTLExpiration:
    """Test TTL expiration behavior and automatic cleanup."""

    @pytest.mark.unit()
    def test_get_expired_secret_returns_none(self) -> None:
        """
        Get on expired secret returns None.

        Verifies that cache.get() returns None after TTL expiration and
        automatically removes expired entry from cache.
        """
        cache = SecretCache(ttl=timedelta(seconds=0.1))
        cache.set("test/secret", "value123")

        # Wait for TTL expiration
        time.sleep(0.2)

        result = cache.get("test/secret")
        assert result is None

        # Verify expired entry was removed from cache
        assert len(cache) == 0

    @pytest.mark.unit()
    def test_get_before_expiration_returns_value(self) -> None:
        """
        Get before TTL expiration returns cached value.

        Verifies that cache.get() returns value before TTL expires (cache hit
        within TTL window).
        """
        cache = SecretCache(ttl=timedelta(seconds=1))
        cache.set("test/secret", "value123")

        # Immediately retrieve (within TTL)
        result = cache.get("test/secret")
        assert result == "value123"

    @pytest.mark.unit()
    def test_set_resets_ttl_timer(self) -> None:
        """
        Set resets TTL timer for existing key.

        Verifies that calling cache.set() on existing key resets the TTL timer
        (extends cache lifetime).
        """
        cache = SecretCache(ttl=timedelta(seconds=0.2))
        cache.set("test/secret", "value123")

        # Wait 0.1s (halfway through TTL)
        time.sleep(0.1)

        # Reset TTL by setting again
        cache.set("test/secret", "value123")

        # Wait another 0.15s (would have expired without reset)
        time.sleep(0.15)

        result = cache.get("test/secret")
        assert result == "value123"  # Still valid due to TTL reset

    @pytest.mark.unit()
    def test_custom_ttl_configuration(self) -> None:
        """
        Custom TTL duration can be configured.

        Verifies that SecretCache accepts custom TTL duration (not restricted
        to default 1 hour).
        """
        cache = SecretCache(ttl=timedelta(minutes=30))
        cache.set("test/secret", "value123")

        # Verify secret is cached (TTL not expired)
        result = cache.get("test/secret")
        assert result == "value123"

    @pytest.mark.unit()
    def test_expired_entries_cleaned_on_access(self) -> None:
        """
        Expired entries removed from cache on access.

        Verifies that cache automatically removes expired entries when accessed
        (lazy cleanup, not periodic background cleanup).
        """
        cache = SecretCache(ttl=timedelta(seconds=0.1))
        cache.set("secret1", "value1")
        cache.set("secret2", "value2")

        assert len(cache) == 2

        # Wait for TTL expiration
        time.sleep(0.2)

        # Access secret1 triggers cleanup for secret1
        cache.get("secret1")

        # secret1 removed, but secret2 still in cache (not accessed yet)
        assert len(cache) == 1

        # Access secret2 triggers cleanup for secret2
        cache.get("secret2")

        # Both secrets removed now
        assert len(cache) == 0


class TestSecretCacheInvalidation:
    """Test explicit cache invalidation and clearing."""

    @pytest.mark.unit()
    def test_invalidate_removes_cached_secret(self) -> None:
        """
        Invalidate removes specific secret from cache.

        Verifies that cache.invalidate() removes cached secret immediately
        (useful for security events like 401 errors).
        """
        cache = SecretCache()
        cache.set("test/secret", "value123")

        cache.invalidate("test/secret")

        result = cache.get("test/secret")
        assert result is None

    @pytest.mark.unit()
    def test_invalidate_nonexistent_secret_no_error(self) -> None:
        """
        Invalidate on nonexistent key does not raise error.

        Verifies that cache.invalidate() is idempotent (safe to call multiple
        times or on missing keys).
        """
        cache = SecretCache()

        # Should not raise exception
        cache.invalidate("nonexistent/secret")

    @pytest.mark.unit()
    def test_invalidate_does_not_affect_other_secrets(self) -> None:
        """
        Invalidate only removes specified secret.

        Verifies that cache.invalidate() does not affect other cached secrets
        (selective invalidation).
        """
        cache = SecretCache()
        cache.set("secret1", "value1")
        cache.set("secret2", "value2")

        cache.invalidate("secret1")

        assert cache.get("secret1") is None
        assert cache.get("secret2") == "value2"

    @pytest.mark.unit()
    def test_clear_removes_all_secrets(self) -> None:
        """
        Clear removes all cached secrets.

        Verifies that cache.clear() removes all entries from cache (useful for
        service restart or security events requiring full cache invalidation).
        """
        cache = SecretCache()
        cache.set("secret1", "value1")
        cache.set("secret2", "value2")
        cache.set("secret3", "value3")

        cache.clear()

        assert cache.get("secret1") is None
        assert cache.get("secret2") is None
        assert cache.get("secret3") is None
        assert len(cache) == 0


class TestSecretCacheThreadSafety:
    """Test thread safety under concurrent access."""

    @pytest.mark.unit()
    def test_concurrent_set_operations(self) -> None:
        """
        Concurrent set operations are thread-safe.

        Verifies that multiple threads can call cache.set() concurrently without
        race conditions or data corruption.
        """
        cache = SecretCache()
        threads = []

        def set_secret(secret_name: str, value: str) -> None:
            cache.set(secret_name, value)

        # Start 10 threads setting different secrets
        for i in range(10):
            thread = Thread(target=set_secret, args=(f"secret{i}", f"value{i}"))
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify all secrets were stored correctly
        for i in range(10):
            assert cache.get(f"secret{i}") == f"value{i}"

    @pytest.mark.unit()
    def test_concurrent_get_operations(self) -> None:
        """
        Concurrent get operations are thread-safe.

        Verifies that multiple threads can call cache.get() concurrently without
        deadlocks or race conditions.
        """
        cache = SecretCache()
        cache.set("shared_secret", "shared_value")

        results: list[str | None] = []

        def get_secret() -> None:
            value = cache.get("shared_secret")
            results.append(value)

        threads = []

        # Start 10 threads getting same secret
        for _ in range(10):
            thread = Thread(target=get_secret)
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify all threads got correct value
        assert len(results) == 10
        assert all(result == "shared_value" for result in results)

    @pytest.mark.unit()
    def test_concurrent_set_and_get(self) -> None:
        """
        Concurrent set and get operations are thread-safe.

        Verifies that mixed read/write operations from multiple threads do not
        cause race conditions or inconsistent state.
        """
        cache = SecretCache()

        read_results: list[str | None] = []

        def writer() -> None:
            for i in range(5):
                cache.set(f"secret{i}", f"value{i}")
                time.sleep(0.01)

        def reader() -> None:
            for i in range(5):
                value = cache.get(f"secret{i}")
                read_results.append(value)
                time.sleep(0.01)

        writer_thread = Thread(target=writer)
        reader_thread = Thread(target=reader)

        writer_thread.start()
        time.sleep(0.005)  # Start reader slightly after writer
        reader_thread.start()

        writer_thread.join()
        reader_thread.join()

        # Reader should either see None (race: get before set) or correct value
        # No corrupted data or deadlocks
        assert len(read_results) == 5
        assert all(result is None or result.startswith("value") for result in read_results)

    @pytest.mark.unit()
    def test_concurrent_invalidate_operations(self) -> None:
        """
        Concurrent invalidate operations are thread-safe.

        Verifies that multiple threads can call cache.invalidate() concurrently
        without race conditions.
        """
        cache = SecretCache()

        # Pre-populate cache
        for i in range(10):
            cache.set(f"secret{i}", f"value{i}")

        threads = []

        def invalidate_secret(secret_name: str) -> None:
            cache.invalidate(secret_name)

        # Start 10 threads invalidating different secrets
        for i in range(10):
            thread = Thread(target=invalidate_secret, args=(f"secret{i}",))
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify all secrets were invalidated
        for i in range(10):
            assert cache.get(f"secret{i}") is None

    @pytest.mark.unit()
    def test_concurrent_clear_operations(self) -> None:
        """
        Concurrent clear operations are thread-safe.

        Verifies that multiple threads can call cache.clear() concurrently without
        race conditions or deadlocks.
        """
        cache = SecretCache()

        def clear_cache() -> None:
            # Pre-populate before clearing
            cache.set("secret1", "value1")
            cache.set("secret2", "value2")
            cache.clear()

        threads = []

        # Start 5 threads clearing cache
        for _ in range(5):
            thread = Thread(target=clear_cache)
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # Verify cache is empty (no deadlock or race condition)
        assert len(cache) == 0
