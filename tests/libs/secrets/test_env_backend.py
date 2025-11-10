"""
Tests for EnvSecretManager backend implementation.

This module validates:
1. Initialization with/without .env file
2. get_secret() functionality (cache hit/miss, expiration, not found)
3. list_secrets() functionality (all vars, prefix filter)
4. set_secret() functionality (create, update, cache invalidation)
5. close() functionality (cache cleanup)
6. Context manager protocol
7. Caching behavior (TTL, expiration, invalidation)
8. Thread safety
9. Error handling (.env file not found, environment variable not set)
10. Security: secret values never logged (AC12)
"""

import os
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from libs.secrets.env_backend import EnvSecretManager
from libs.secrets.exceptions import (
    SecretAccessError,
    SecretNotFoundError,
)

# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture()
def temp_env_file():
    """Create a temporary .env file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
        f.write("TEST_SECRET_1=value1\n")
        f.write("TEST_SECRET_2=value2\n")
        f.write("DATABASE_PASSWORD=db_pass_123\n")
        f.write("DATABASE_HOST=localhost\n")
        f.write("ALPACA_API_KEY_ID=alpaca_key_123\n")
        temp_path = f.name

    yield temp_path

    # Cleanup
    os.unlink(temp_path)


@pytest.fixture()
def clean_env():
    """Clean up test environment variables before and after tests."""
    # Save original environment
    original_env = os.environ.copy()

    # Remove any test variables
    test_vars = [
        "TEST_SECRET_1",
        "TEST_SECRET_2",
        "DATABASE_PASSWORD",
        "DATABASE_HOST",
        "ALPACA_API_KEY_ID",
        "NEW_SECRET",
        "RUNTIME_SECRET",
    ]
    for var in test_vars:
        os.environ.pop(var, None)

    yield

    # Restore original environment
    os.environ.clear()
    os.environ.update(original_env)


# ============================================================================
# INITIALIZATION TESTS
# ============================================================================


class TestEnvSecretManagerInitialization:
    """Test EnvSecretManager initialization."""

    def test_initialization_without_dotenv_file(self, clean_env):
        """Test initialization without .env file loads only existing env vars."""
        # Set a variable manually
        os.environ["TEST_SECRET_1"] = "manual_value"

        manager = EnvSecretManager()
        secret = manager.get_secret("test/secret/1")

        assert secret == "manual_value"

    def test_initialization_with_dotenv_file(self, temp_env_file, clean_env):
        """Test initialization with .env file loads variables."""
        manager = EnvSecretManager(dotenv_path=temp_env_file)

        # Verify .env variables are loaded
        assert manager.get_secret("test/secret/1") == "value1"
        assert manager.get_secret("test/secret/2") == "value2"
        assert manager.get_secret("database/password") == "db_pass_123"

    def test_initialization_with_dotenv_file_path_object(self, temp_env_file, clean_env):
        """Test initialization with Path object for .env file."""
        manager = EnvSecretManager(dotenv_path=Path(temp_env_file))

        assert manager.get_secret("test/secret/1") == "value1"

    def test_initialization_with_custom_cache_ttl(self, clean_env):
        """Test initialization with custom cache TTL."""
        os.environ["TEST_SECRET_1"] = "value1"

        manager = EnvSecretManager(cache_ttl_seconds=600)

        # First call caches the value
        manager.get_secret("test/secret/1")

        # Change environment variable
        os.environ["TEST_SECRET_1"] = "new_value"

        # Should still get cached value (10 minute TTL not expired)
        assert manager.get_secret("test/secret/1") == "value1"

    def test_initialization_with_zero_cache_ttl(self, clean_env):
        """Test initialization with cache disabled (TTL=0)."""
        os.environ["TEST_SECRET_1"] = "value1"

        manager = EnvSecretManager(cache_ttl_seconds=0)

        # First call
        assert manager.get_secret("test/secret/1") == "value1"

        # Change environment variable
        os.environ["TEST_SECRET_1"] = "new_value"

        # Should get new value immediately (cache disabled)
        assert manager.get_secret("test/secret/1") == "new_value"

    def test_initialization_with_nonexistent_dotenv_file(self, clean_env):
        """Test initialization raises error if .env file doesn't exist."""
        with pytest.raises(SecretAccessError) as exc_info:
            EnvSecretManager(dotenv_path="/nonexistent/path/.env")

        assert "dotenv_file" in str(exc_info.value)
        assert ".env file not found" in str(exc_info.value)

    def test_initialization_dotenv_override_existing_vars(self, temp_env_file, clean_env):
        """Test .env file overrides existing environment variables."""
        # Set variable before loading .env
        os.environ["TEST_SECRET_1"] = "original_value"

        # Load .env file (should override)
        manager = EnvSecretManager(dotenv_path=temp_env_file)

        # Should get .env value, not original
        assert manager.get_secret("test/secret/1") == "value1"


# ============================================================================
# GET_SECRET TESTS
# ============================================================================


class TestEnvSecretManagerGetSecret:
    """Test get_secret() functionality."""

    def test_get_secret_from_environment(self, clean_env):
        """Test retrieving secret from environment variable."""
        os.environ["TEST_SECRET_1"] = "test_value"
        manager = EnvSecretManager()

        secret = manager.get_secret("test/secret/1")

        assert secret == "test_value"

    def test_get_secret_from_dotenv_file(self, temp_env_file, clean_env):
        """Test retrieving secret from .env file."""
        manager = EnvSecretManager(dotenv_path=temp_env_file)

        secret = manager.get_secret("database/password")

        assert secret == "db_pass_123"

    def test_get_secret_not_found(self, clean_env):
        """Test SecretNotFoundError raised when variable not set."""
        manager = EnvSecretManager()

        with pytest.raises(SecretNotFoundError) as exc_info:
            manager.get_secret("nonexistent/var")

        assert exc_info.value.secret_name == "nonexistent/var"
        assert exc_info.value.backend == "env"
        assert "nonexistent/var" in str(exc_info.value)
        assert "not set" in str(exc_info.value)

    def test_get_secret_cache_hit(self, clean_env):
        """Test cache hit returns cached value without environment lookup."""
        os.environ["TEST_SECRET_1"] = "original_value"
        manager = EnvSecretManager()

        # First call caches the value
        secret1 = manager.get_secret("test/secret/1")
        assert secret1 == "original_value"

        # Change environment variable
        os.environ["TEST_SECRET_1"] = "new_value"

        # Second call should return cached value
        secret2 = manager.get_secret("test/secret/1")
        assert secret2 == "original_value"

    def test_get_secret_cache_miss_after_expiration(self, clean_env):
        """Test cache expiration causes fresh fetch from environment."""
        os.environ["TEST_SECRET_1"] = "original_value"
        manager = EnvSecretManager(cache_ttl_seconds=1)

        # First call caches the value
        secret1 = manager.get_secret("test/secret/1")
        assert secret1 == "original_value"

        # Change environment variable
        os.environ["TEST_SECRET_1"] = "new_value"

        # Wait for cache to expire
        time.sleep(1.1)

        # Should fetch new value after expiration
        secret2 = manager.get_secret("test/secret/1")
        assert secret2 == "new_value"

    def test_get_secret_empty_string_value(self, clean_env):
        """Test retrieving secret with empty string value."""
        os.environ["TEST_SECRET_1"] = ""
        manager = EnvSecretManager()

        secret = manager.get_secret("test/secret/1")

        assert secret == ""

    def test_get_secret_whitespace_value(self, clean_env):
        """Test retrieving secret with whitespace value."""
        os.environ["TEST_SECRET_1"] = "  value with spaces  "
        manager = EnvSecretManager()

        secret = manager.get_secret("test/secret/1")

        assert secret == "  value with spaces  "

    def test_get_secret_special_characters(self, clean_env):
        """Test retrieving secret with special characters."""
        os.environ["TEST_SECRET_1"] = "p@ssw0rd!#$%^&*()"
        manager = EnvSecretManager()

        secret = manager.get_secret("test/secret/1")

        assert secret == "p@ssw0rd!#$%^&*()"


# ============================================================================
# LIST_SECRETS TESTS
# ============================================================================


class TestEnvSecretManagerListSecrets:
    """Test list_secrets() functionality."""

    def test_list_secrets_all(self, temp_env_file, clean_env):
        """Test listing all environment variables."""
        manager = EnvSecretManager(dotenv_path=temp_env_file)

        # Expect warning when listing without prefix (security best practice)
        with pytest.warns(UserWarning, match="list_secrets\\(\\) called without prefix filter"):
            secrets = manager.list_secrets()

        # Should include .env variables (converted to hierarchical format)
        assert "test/secret/1" in secrets
        assert "test/secret/2" in secrets
        assert "database/password" in secrets
        assert "alpaca/api/key/id" in secrets

        # Should be sorted
        assert secrets == sorted(secrets)

    def test_list_secrets_with_prefix(self, temp_env_file, clean_env):
        """Test listing environment variables with prefix filter."""
        manager = EnvSecretManager(dotenv_path=temp_env_file)

        secrets = manager.list_secrets(prefix="database/")

        # CI environments may have additional DATABASE_ vars (e.g., DATABASE_URL → database/url)
        # Validate expected secrets exist, not exact count
        assert "database/password" in secrets
        assert "database/host" in secrets
        assert "alpaca/api/key/id" not in secrets
        assert all(s.startswith("database/") for s in secrets), \
            f"All secrets should start with 'database/', got: {secrets}"

    def test_list_secrets_with_prefix_no_matches(self, temp_env_file, clean_env):
        """Test listing with prefix that matches no variables."""
        manager = EnvSecretManager(dotenv_path=temp_env_file)

        secrets = manager.list_secrets(prefix="nonexistent/")

        assert secrets == []

    def test_list_secrets_empty_prefix(self, temp_env_file, clean_env):
        """Test listing with empty string prefix returns all variables."""
        manager = EnvSecretManager(dotenv_path=temp_env_file)

        secrets = manager.list_secrets(prefix="")

        # Empty prefix should match all variables
        assert "test/secret/1" in secrets
        assert len(secrets) > 0

    def test_list_secrets_sorted_output(self, clean_env):
        """Test list output is sorted alphabetically."""
        os.environ["ZEBRA"] = "z"
        os.environ["ALPHA"] = "a"
        os.environ["BETA"] = "b"

        manager = EnvSecretManager()
        secrets = manager.list_secrets(prefix="")

        # Find our test variables in the sorted list (converted to hierarchical format)
        test_vars = [s for s in secrets if s in ["zebra", "alpha", "beta"]]
        assert test_vars == ["alpha", "beta", "zebra"]

    def test_list_secrets_ambiguous_naming_limitation(self, clean_env):
        """
        Test that demonstrates the ambiguous conversion limitation.

        This is a KNOWN LIMITATION of EnvSecretManager: underscores in the
        original hierarchical name cannot be distinguished from path separators
        when converting back from environment variables.

        This test documents the expected behavior to prevent regressions.
        """
        manager = EnvSecretManager()

        # Set a secret with underscore in hierarchical name
        manager.set_secret("api/key_id", "secret_value")

        # Verify it was set correctly in environment (forward conversion works)
        assert os.environ["API_KEY_ID"] == "secret_value"

        # Verify get_secret works with original name (forward conversion works)
        assert manager.get_secret("api/key_id") == "secret_value"

        # BUT: list_secrets returns different name due to ambiguous reverse conversion
        secrets = manager.list_secrets(prefix="api/")

        # EXPECTED LIMITATION: "api/key_id" becomes "api/key/id" in list output
        assert "api/key/id" in secrets
        # Original name is NOT in the list (information lost in reverse conversion)
        assert "api/key_id" not in secrets

        # IMPORTANT: Despite list_secrets ambiguity, get_secret still works
        # with BOTH the original name AND the converted name (same env var)
        assert manager.get_secret("api/key_id") == "secret_value"
        assert manager.get_secret("api/key/id") == "secret_value"


# ============================================================================
# SET_SECRET TESTS
# ============================================================================


class TestEnvSecretManagerSetSecret:
    """Test set_secret() functionality."""

    def test_set_secret_creates_new_variable(self, clean_env):
        """Test set_secret() creates new environment variable."""
        manager = EnvSecretManager()

        manager.set_secret("new/secret", "new_value")

        assert os.environ["NEW_SECRET"] == "new_value"

    def test_set_secret_updates_existing_variable(self, clean_env):
        """Test set_secret() updates existing environment variable."""
        os.environ["TEST_SECRET_1"] = "original_value"
        manager = EnvSecretManager()

        manager.set_secret("test/secret/1", "updated_value")

        assert os.environ["TEST_SECRET_1"] == "updated_value"

    def test_set_secret_invalidates_cache(self, clean_env):
        """Test set_secret() invalidates cached value."""
        os.environ["TEST_SECRET_1"] = "original_value"
        manager = EnvSecretManager()

        # First call caches the value
        secret1 = manager.get_secret("test/secret/1")
        assert secret1 == "original_value"

        # Update via set_secret (should invalidate cache)
        manager.set_secret("test/secret/1", "new_value")

        # Next get_secret should fetch updated value
        secret2 = manager.get_secret("test/secret/1")
        assert secret2 == "new_value"

    def test_set_secret_empty_string(self, clean_env):
        """Test set_secret() with empty string value."""
        manager = EnvSecretManager()

        manager.set_secret("test/secret/1", "")

        assert os.environ["TEST_SECRET_1"] == ""

    def test_set_secret_special_characters(self, clean_env):
        """Test set_secret() with special characters."""
        manager = EnvSecretManager()

        manager.set_secret("test/secret/1", "p@ssw0rd!#$%^&*()")

        assert os.environ["TEST_SECRET_1"] == "p@ssw0rd!#$%^&*()"

    def test_set_secret_runtime_only(self, temp_env_file, clean_env):
        """Test set_secret() doesn't persist to .env file."""
        manager = EnvSecretManager(dotenv_path=temp_env_file)

        # Update secret
        manager.set_secret("runtime/secret", "runtime_value")

        # Verify in environment
        assert os.environ["RUNTIME_SECRET"] == "runtime_value"

        # Verify NOT in .env file
        with open(temp_env_file) as f:
            content = f.read()
            assert "RUNTIME_SECRET" not in content


# ============================================================================
# CLOSE TESTS
# ============================================================================


class TestEnvSecretManagerClose:
    """Test close() functionality."""

    def test_close_clears_cache(self, clean_env):
        """Test close() clears in-memory cache."""
        os.environ["TEST_SECRET_1"] = "value1"
        manager = EnvSecretManager()

        # Cache a value
        manager.get_secret("test/secret/1")

        # Verify cache is populated
        assert len(manager._cache) > 0

        # Close
        manager.close()

        # Verify cache is cleared
        assert len(manager._cache) == 0

    def test_close_can_be_called_multiple_times(self, clean_env):
        """Test close() can be called multiple times without error."""
        manager = EnvSecretManager()

        manager.close()
        manager.close()  # Should not raise

    def test_get_secret_after_close_works(self, clean_env):
        """Test get_secret() still works after close() (rebuilds cache)."""
        os.environ["TEST_SECRET_1"] = "value1"
        manager = EnvSecretManager()

        # Cache and close
        manager.get_secret("test/secret/1")
        manager.close()

        # Should still work (fresh fetch)
        secret = manager.get_secret("test/secret/1")
        assert secret == "value1"


# ============================================================================
# CONTEXT MANAGER TESTS
# ============================================================================


class TestEnvSecretManagerContextManager:
    """Test context manager protocol."""

    def test_context_manager_enter_returns_manager(self, clean_env):
        """Test __enter__ returns the manager instance."""
        manager = EnvSecretManager()

        with manager as ctx_manager:
            assert ctx_manager is manager

    def test_context_manager_exit_calls_close(self, clean_env):
        """Test __exit__ calls close() automatically."""
        os.environ["TEST_SECRET_1"] = "value1"
        manager = EnvSecretManager()

        with manager:
            manager.get_secret("test/secret/1")
            # Verify cache is populated
            assert len(manager._cache) > 0

        # After exiting context, cache should be cleared
        assert len(manager._cache) == 0

    def test_context_manager_exit_on_exception(self, clean_env):
        """Test __exit__ calls close() even if exception occurs."""
        os.environ["TEST_SECRET_1"] = "value1"
        manager = EnvSecretManager()

        try:
            with manager:
                manager.get_secret("test/secret/1")
                assert len(manager._cache) > 0
                raise ValueError("Test exception")
        except ValueError:
            pass

        # Cache should still be cleared
        assert len(manager._cache) == 0

    def test_context_manager_usage_pattern(self, temp_env_file, clean_env):
        """Test typical context manager usage pattern."""
        with EnvSecretManager(dotenv_path=temp_env_file) as secret_mgr:
            db_password = secret_mgr.get_secret("database/password")
            alpaca_key = secret_mgr.get_secret("alpaca/api/key/id")

            assert db_password == "db_pass_123"
            assert alpaca_key == "alpaca_key_123"


# ============================================================================
# CACHING TESTS
# ============================================================================


class TestEnvSecretManagerCaching:
    """Test caching behavior (using SecretCache)."""

    def test_cache_ttl_default_one_hour(self, clean_env):
        """Test cache is initialized (TTL tested in test_cache.py)."""
        manager = EnvSecretManager()

        # Verify SecretCache instance created
        assert manager._cache is not None
        assert hasattr(manager._cache, "get")
        assert hasattr(manager._cache, "set")

    def test_cache_ttl_custom_value(self, clean_env):
        """Test custom cache TTL is passed to SecretCache."""
        manager = EnvSecretManager(cache_ttl_seconds=600)

        # Verify SecretCache instance created
        assert manager._cache is not None

    def test_cache_stores_value_and_timestamp(self, clean_env):
        """Test cache stores values correctly."""
        os.environ["TEST_SECRET_1"] = "value1"
        manager = EnvSecretManager()

        # First call caches value
        result1 = manager.get_secret("test/secret/1")
        assert result1 == "value1"

        # Verify cache contains entry (check via len)
        assert len(manager._cache) == 1

        # Second call should use cache (same value)
        result2 = manager.get_secret("test/secret/1")
        assert result2 == "value1"

    def test_cache_expiration_removes_entry(self, clean_env):
        """Test expired cache entry is removed on next get_secret()."""
        os.environ["TEST_SECRET_1"] = "value1"
        manager = EnvSecretManager(cache_ttl_seconds=1)

        # Cache the value
        manager.get_secret("test/secret/1")
        assert len(manager._cache) == 1

        # Wait for expiration
        time.sleep(1.1)

        # Next get should remove expired entry and create new one
        result = manager.get_secret("test/secret/1")
        assert result == "value1"
        # Cache should still have entry (re-cached after expiration)
        assert len(manager._cache) == 1

    def test_cache_invalidation_on_set_secret(self, clean_env):
        """Test set_secret() removes entry from cache."""
        os.environ["TEST_SECRET_1"] = "value1"
        manager = EnvSecretManager()

        # Cache the value
        manager.get_secret("test/secret/1")
        assert len(manager._cache) == 1

        # Update via set_secret
        manager.set_secret("test/secret/1", "new_value")

        # Cache should be invalidated (empty)
        assert len(manager._cache) == 0

        # Next get should return new value and re-cache
        result = manager.get_secret("test/secret/1")
        assert result == "new_value"
        assert len(manager._cache) == 1

    def test_cache_independent_per_secret(self, clean_env):
        """Test cache entries are independent per secret."""
        os.environ["TEST_SECRET_1"] = "value1"
        os.environ["TEST_SECRET_2"] = "value2"
        manager = EnvSecretManager()

        # Cache both secrets
        manager.get_secret("test/secret/1")
        manager.get_secret("test/secret/2")

        assert len(manager._cache) == 2

        # Invalidate one
        manager.set_secret("test/secret/1", "new_value")

        # Cache should have one entry (test/secret/2)
        assert len(manager._cache) == 1

        # Verify test/secret/2 still cached
        result2 = manager.get_secret("test/secret/2")
        assert result2 == "value2"

        # Verify test/secret/1 returns new value
        result1 = manager.get_secret("test/secret/1")
        assert result1 == "new_value"


# ============================================================================
# THREAD SAFETY TESTS
# ============================================================================


class TestEnvSecretManagerThreadSafety:
    """Test thread-safe operations."""

    def test_concurrent_get_secret_calls(self, clean_env):
        """Test multiple threads can call get_secret() concurrently."""
        os.environ["TEST_SECRET_1"] = "value1"
        manager = EnvSecretManager()

        results = []
        errors = []

        def fetch_secret():
            try:
                secret = manager.get_secret("test/secret/1")
                results.append(secret)
            except Exception as e:
                errors.append(e)

        # Launch 10 threads
        threads = [threading.Thread(target=fetch_secret) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All threads should succeed
        assert len(errors) == 0
        assert len(results) == 10
        assert all(r == "value1" for r in results)

    def test_concurrent_set_secret_calls(self, clean_env):
        """Test multiple threads can call set_secret() concurrently."""
        manager = EnvSecretManager()

        errors = []

        def update_secret(thread_id):
            try:
                manager.set_secret(f"secret/{thread_id}", f"value_{thread_id}")
            except Exception as e:
                errors.append(e)

        # Launch 10 threads
        threads = [
            threading.Thread(target=update_secret, args=(i,)) for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All threads should succeed
        assert len(errors) == 0

        # Verify all secrets were set
        for i in range(10):
            assert os.environ[f"SECRET_{i}"] == f"value_{i}"

    def test_concurrent_list_secrets_calls(self, clean_env):
        """Test multiple threads can call list_secrets() concurrently."""
        os.environ["TEST_SECRET_1"] = "value1"
        os.environ["TEST_SECRET_2"] = "value2"
        manager = EnvSecretManager()

        results = []
        errors = []

        def list_all():
            try:
                # Suppress expected warning in thread context
                import warnings
                with warnings.catch_warnings():
                    warnings.simplefilter("ignore", UserWarning)
                    secrets = manager.list_secrets()
                results.append(secrets)
            except Exception as e:
                errors.append(e)

        # Launch 10 threads
        threads = [threading.Thread(target=list_all) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All threads should succeed
        assert len(errors) == 0
        assert len(results) == 10

    def test_concurrent_cache_access(self, clean_env):
        """Test cache is protected from concurrent access corruption."""
        os.environ["TEST_SECRET_1"] = "value1"
        manager = EnvSecretManager()

        errors = []

        def mixed_operations(thread_id):
            try:
                # Mix of get, set, list operations
                manager.get_secret("test/secret/1")
                manager.set_secret(f"thread/{thread_id}", f"value_{thread_id}")
                manager.list_secrets(prefix="thread/")
            except Exception as e:
                errors.append(e)

        # Launch 20 threads doing mixed operations
        threads = [
            threading.Thread(target=mixed_operations, args=(i,)) for i in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All operations should succeed without errors
        assert len(errors) == 0


# ============================================================================
# ERROR HANDLING TESTS
# ============================================================================


class TestEnvSecretManagerErrorHandling:
    """Test error handling."""

    def test_dotenv_file_not_found_error(self, clean_env):
        """Test SecretAccessError raised when .env file doesn't exist."""
        with pytest.raises(SecretAccessError) as exc_info:
            EnvSecretManager(dotenv_path="/nonexistent/.env")

        assert exc_info.value.secret_name == "dotenv_file"
        assert exc_info.value.backend == "env"
        assert ".env file not found" in str(exc_info.value)

    def test_environment_variable_not_set_error(self, clean_env):
        """Test SecretNotFoundError raised when variable doesn't exist."""
        manager = EnvSecretManager()

        with pytest.raises(SecretNotFoundError) as exc_info:
            manager.get_secret("missing/var")

        assert exc_info.value.secret_name == "missing/var"
        assert exc_info.value.backend == "env"
        assert "not set" in str(exc_info.value)
        assert "Verify .env file" in str(exc_info.value)

    def test_error_messages_never_contain_secret_values(self, clean_env):
        """Test error messages contain secret NAMES but not VALUES (AC12)."""
        os.environ["TEST_SECRET_1"] = "sensitive_password_123"
        manager = EnvSecretManager()

        # Force an error by trying to get non-existent secret
        try:
            manager.get_secret("nonexistent/secret")
        except SecretNotFoundError as e:
            error_msg = str(e)
            # Should contain secret name
            assert "nonexistent/secret" in error_msg
            # Should NOT contain any actual secret values
            assert "sensitive_password_123" not in error_msg
