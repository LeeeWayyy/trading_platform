"""Tests for VaultSecretManager backend implementation."""

import threading
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, Mock, call, patch

import pytest
from hvac.exceptions import (
    Forbidden,
    InvalidPath,
    InvalidRequest,
    Unauthorized,
    VaultDown,
    VaultError,
)

from libs.secrets.exceptions import (
    SecretAccessError,
    SecretNotFoundError,
    SecretWriteError,
)
from libs.secrets.vault_backend import VaultSecretManager

# ================================================================================
# Fixtures
# ================================================================================


@pytest.fixture(autouse=True)
def fast_retry_sleep(monkeypatch):
    """Eliminate retry backoff delays in tests to keep the suite fast."""
    monkeypatch.setattr("tenacity.nap.sleep", lambda *args, **kwargs: None)


@pytest.fixture()
def mock_hvac_client():
    """Create a mock hvac client for testing."""
    client = MagicMock()
    client.is_authenticated.return_value = True
    client.sys.is_sealed.return_value = False  # Use proper hvac v2 API
    client.secrets.kv.v2 = MagicMock()
    return client


@pytest.fixture()
def vault_secret_mgr(mock_hvac_client):
    """Create a VaultSecretManager instance with mocked hvac client."""
    with patch("libs.secrets.vault_backend.hvac.Client", return_value=mock_hvac_client):
        secret_mgr = VaultSecretManager(
            vault_url="https://vault.example.com:8200",
            token="s.test_token",
            mount_point="kv",
            cache_ttl_seconds=3600,
        )
    return secret_mgr


# ================================================================================
# Test Initialization
# ================================================================================


class TestVaultSecretManagerInitialization:
    """Test VaultSecretManager initialization scenarios."""

    def test_init_successful_connection(self, mock_hvac_client):
        """Test successful initialization with valid credentials."""
        with patch("libs.secrets.vault_backend.hvac.Client", return_value=mock_hvac_client):
            secret_mgr = VaultSecretManager(
                vault_url="https://vault.example.com:8200",
                token="s.test_token",
            )

        assert secret_mgr._client == mock_hvac_client
        assert secret_mgr._vault_url == "https://vault.example.com:8200"
        assert secret_mgr._mount_point == "kv"
        assert secret_mgr._cache is not None  # SecretCache instance
        assert len(secret_mgr._cache) == 0  # Empty cache
        assert secret_mgr._verify is True
        mock_hvac_client.is_authenticated.assert_called_once()

    def test_init_custom_mount_point(self, mock_hvac_client):
        """Test initialization with custom KV mount point."""
        with patch("libs.secrets.vault_backend.hvac.Client", return_value=mock_hvac_client):
            secret_mgr = VaultSecretManager(
                vault_url="https://vault.example.com:8200",
                token="s.test_token",
                mount_point="secret",
            )

        assert secret_mgr._mount_point == "secret"

    def test_init_custom_cache_ttl(self, mock_hvac_client):
        """Test initialization with custom cache TTL."""
        with patch("libs.secrets.vault_backend.hvac.Client", return_value=mock_hvac_client):
            secret_mgr = VaultSecretManager(
                vault_url="https://vault.example.com:8200",
                token="s.test_token",
                cache_ttl_seconds=1800,
            )

        # Verify SecretCache instance created (TTL verified internally)
        assert secret_mgr._cache is not None

    def test_init_disable_tls_verification(self, mock_hvac_client):
        """Test initialization with TLS verification disabled (local dev only)."""
        with patch("libs.secrets.vault_backend.hvac.Client", return_value=mock_hvac_client):
            secret_mgr = VaultSecretManager(
                vault_url="https://localhost:8200",
                token="s.test_token",
                verify=False,
            )

        assert secret_mgr._verify is False

    def test_init_auth_failure(self):
        """Test initialization failure when authentication fails."""
        mock_client = MagicMock()
        mock_client.is_authenticated.return_value = False
        mock_client.seal_status = {"sealed": False}

        with patch("libs.secrets.vault_backend.hvac.Client", return_value=mock_client):
            with pytest.raises(SecretAccessError) as exc_info:
                VaultSecretManager(
                    vault_url="https://vault.example.com:8200",
                    token="s.bad_token",
                )

        assert "vault_auth" in str(exc_info.value.secret_name)
        assert "authentication failed" in str(exc_info.value).lower()

    def test_init_sealed_vault(self):
        """Test initialization failure when Vault is sealed."""
        mock_client = MagicMock()
        mock_client.is_authenticated.return_value = True
        mock_client.seal_status = {"sealed": True}

        with patch("libs.secrets.vault_backend.hvac.Client", return_value=mock_client):
            with pytest.raises(SecretAccessError) as exc_info:
                VaultSecretManager(
                    vault_url="https://vault.example.com:8200",
                    token="s.test_token",
                )

        assert "vault_status" in str(exc_info.value.secret_name)
        assert "sealed" in str(exc_info.value).lower()

    def test_init_vault_unreachable(self):
        """Test initialization failure when Vault server is unreachable."""
        with patch(
            "libs.secrets.vault_backend.hvac.Client",
            side_effect=VaultDown("Connection refused"),
        ):
            with pytest.raises(SecretAccessError) as exc_info:
                VaultSecretManager(
                    vault_url="https://vault.example.com:8200",
                    token="s.test_token",
                )

        assert "vault_connectivity" in str(exc_info.value.secret_name)
        assert "unreachable" in str(exc_info.value).lower()

    def test_init_unauthorized(self):
        """Test initialization failure with unauthorized token."""
        with patch(
            "libs.secrets.vault_backend.hvac.Client",
            side_effect=Unauthorized("Invalid token"),
        ):
            with pytest.raises(SecretAccessError) as exc_info:
                VaultSecretManager(
                    vault_url="https://vault.example.com:8200",
                    token="s.bad_token",
                )

        assert "vault_auth" in str(exc_info.value.secret_name)


# ================================================================================
# Test get_secret
# ================================================================================


class TestVaultSecretManagerGetSecret:
    """Test VaultSecretManager.get_secret() method."""

    def test_get_secret_success(self, vault_secret_mgr, mock_hvac_client):
        """Test successful secret retrieval from Vault."""
        mock_hvac_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"value": "my_secret_value"}}
        }

        value = vault_secret_mgr.get_secret("database/password")

        assert value == "my_secret_value"
        mock_hvac_client.secrets.kv.v2.read_secret_version.assert_called_once_with(
            path="database/password",
            mount_point="kv",
        )

    def test_get_secret_multi_key_uses_value_key(self, vault_secret_mgr, mock_hvac_client):
        """Test secret retrieval when KV has multiple keys (uses 'value' key)."""
        mock_hvac_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"value": "primary_value", "backup": "backup_value"}}
        }

        value = vault_secret_mgr.get_secret("database/password")

        assert value == "primary_value"

    def test_get_secret_multi_key_without_value_key(self, vault_secret_mgr, mock_hvac_client):
        """Test secret retrieval when KV has multiple keys (no 'value' key)."""
        mock_hvac_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"password": "pwd123", "username": "admin"}}
        }

        value = vault_secret_mgr.get_secret("database/credentials")

        # Should use first key alphabetically ('password' before 'username')
        assert value == "pwd123"

    def test_get_secret_not_found(self, vault_secret_mgr, mock_hvac_client):
        """Test secret retrieval when secret doesn't exist."""
        mock_hvac_client.secrets.kv.v2.read_secret_version.side_effect = InvalidPath(
            "Secret not found"
        )

        with pytest.raises(SecretNotFoundError) as exc_info:
            vault_secret_mgr.get_secret("nonexistent/secret")

        assert "nonexistent/secret" in str(exc_info.value.secret_name)

    def test_get_secret_empty_data(self, vault_secret_mgr, mock_hvac_client):
        """Test secret retrieval when secret exists but has no data."""
        mock_hvac_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {}}
        }

        with pytest.raises(SecretNotFoundError) as exc_info:
            vault_secret_mgr.get_secret("database/password")

        assert "database/password" in str(exc_info.value.secret_name)
        assert "no data" in str(exc_info.value).lower()

    def test_get_secret_permission_denied(self, vault_secret_mgr, mock_hvac_client):
        """Test secret retrieval when permission is denied."""
        mock_hvac_client.secrets.kv.v2.read_secret_version.side_effect = Forbidden(
            "Permission denied"
        )

        with pytest.raises(SecretAccessError) as exc_info:
            vault_secret_mgr.get_secret("restricted/secret")

        assert "restricted/secret" in str(exc_info.value.secret_name)
        assert "permission denied" in str(exc_info.value).lower()

    def test_get_secret_vault_down(self, vault_secret_mgr, mock_hvac_client):
        """Test secret retrieval when Vault is down."""
        mock_hvac_client.secrets.kv.v2.read_secret_version.side_effect = VaultDown(
            "Vault unreachable"
        )

        with pytest.raises(SecretAccessError) as exc_info:
            vault_secret_mgr.get_secret("database/password")

        assert "unreachable" in str(exc_info.value).lower()

    def test_get_secret_retries_before_failing(self, vault_secret_mgr, mock_hvac_client):
        """Ensure get_secret retries three times on VaultDown."""
        mock_hvac_client.secrets.kv.v2.read_secret_version.side_effect = VaultDown(
            "Vault unreachable"
        )

        with pytest.raises(SecretAccessError):
            vault_secret_mgr.get_secret("database/password")

        assert mock_hvac_client.secrets.kv.v2.read_secret_version.call_count == 3

    def test_get_secret_vault_error(self, vault_secret_mgr, mock_hvac_client):
        """Test secret retrieval when Vault returns an error."""
        mock_hvac_client.secrets.kv.v2.read_secret_version.side_effect = VaultError(
            "Internal error"
        )

        with pytest.raises(SecretAccessError) as exc_info:
            vault_secret_mgr.get_secret("database/password")

        assert "database/password" in str(exc_info.value.secret_name)


# ================================================================================
# Test list_secrets
# ================================================================================


class TestVaultSecretManagerListSecrets:
    """Test VaultSecretManager.list_secrets() method."""

    def test_list_secrets_no_prefix(self, vault_secret_mgr, mock_hvac_client):
        """Test listing all secrets without prefix filter."""
        mock_hvac_client.secrets.kv.v2.list_secrets.side_effect = [
            {"data": {"keys": ["database/", "alpaca/"]}},  # root
            {"data": {"keys": ["api_key", "api_secret"]}},  # alpaca leaf
            {"data": {"keys": ["password", "archive/"]}},  # database branch
            {"data": {"keys": ["old_password"]}},  # nested directory
        ]

        secrets = vault_secret_mgr.list_secrets()

        assert secrets == [
            "alpaca/api_key",
            "alpaca/api_secret",
            "database/archive/old_password",
            "database/password",
        ]
        assert mock_hvac_client.secrets.kv.v2.list_secrets.call_args_list == [
            call(path="", mount_point="kv"),
            call(path="alpaca", mount_point="kv"),
            call(path="database", mount_point="kv"),
            call(path="database/archive", mount_point="kv"),
        ]

    def test_list_secrets_with_prefix(self, vault_secret_mgr, mock_hvac_client):
        """Test listing secrets with prefix filter."""
        mock_hvac_client.secrets.kv.v2.list_secrets.return_value = {
            "data": {"keys": ["password", "host", "port"]}
        }

        secrets = vault_secret_mgr.list_secrets(prefix="database/")

        assert sorted(secrets) == ["database/host", "database/password", "database/port"]
        mock_hvac_client.secrets.kv.v2.list_secrets.assert_called_once_with(
            path="database",
            mount_point="kv",
        )

    def test_list_secrets_empty_directory(self, vault_secret_mgr, mock_hvac_client):
        """Test listing secrets from empty directory."""
        mock_hvac_client.secrets.kv.v2.list_secrets.side_effect = InvalidPath(
            "Path not found"
        )

        secrets = vault_secret_mgr.list_secrets(prefix="empty/")

        assert secrets == []

    def test_list_secrets_permission_denied(self, vault_secret_mgr, mock_hvac_client):
        """Test listing secrets when permission is denied."""
        mock_hvac_client.secrets.kv.v2.list_secrets.side_effect = Forbidden(
            "Permission denied"
        )

        with pytest.raises(SecretAccessError) as exc_info:
            vault_secret_mgr.list_secrets(prefix="restricted/")

        assert "permission denied" in str(exc_info.value).lower()

    def test_list_secrets_vault_down(self, vault_secret_mgr, mock_hvac_client):
        """Test listing secrets when Vault is down."""
        mock_hvac_client.secrets.kv.v2.list_secrets.side_effect = VaultDown(
            "Vault unreachable"
        )

        with pytest.raises(SecretAccessError) as exc_info:
            vault_secret_mgr.list_secrets()

        assert "unreachable" in str(exc_info.value).lower()

    def test_list_secrets_retries_on_vault_down(self, vault_secret_mgr, mock_hvac_client):
        """Ensure list_secrets retries three times on VaultDown."""
        mock_hvac_client.secrets.kv.v2.list_secrets.side_effect = VaultDown(
            "Vault unreachable"
        )

        with pytest.raises(SecretAccessError):
            vault_secret_mgr.list_secrets(prefix="database/")

        assert mock_hvac_client.secrets.kv.v2.list_secrets.call_count == 3


# ================================================================================
# Test set_secret
# ================================================================================


class TestVaultSecretManagerSetSecret:
    """Test VaultSecretManager.set_secret() method."""

    def test_set_secret_create_new(self, vault_secret_mgr, mock_hvac_client):
        """Test creating a new secret in Vault."""
        vault_secret_mgr.set_secret("database/password", "new_password")

        mock_hvac_client.secrets.kv.v2.create_or_update_secret.assert_called_once_with(
            path="database/password",
            secret={"value": "new_password"},
            mount_point="kv",
        )

    def test_set_secret_update_existing(self, vault_secret_mgr, mock_hvac_client):
        """Test updating an existing secret in Vault."""
        # First get the secret (populate cache)
        mock_hvac_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"value": "old_password"}}
        }
        vault_secret_mgr.get_secret("database/password")
        assert len(vault_secret_mgr._cache) == 1  # Cache populated

        # Now update it
        vault_secret_mgr.set_secret("database/password", "new_password")

        mock_hvac_client.secrets.kv.v2.create_or_update_secret.assert_called_once_with(
            path="database/password",
            secret={"value": "new_password"},
            mount_point="kv",
        )

        # Cache should be invalidated (empty)
        assert len(vault_secret_mgr._cache) == 0

    def test_set_secret_permission_denied(self, vault_secret_mgr, mock_hvac_client):
        """Test setting secret when permission is denied."""
        mock_hvac_client.secrets.kv.v2.create_or_update_secret.side_effect = Forbidden(
            "Permission denied"
        )

        with pytest.raises(SecretWriteError) as exc_info:
            vault_secret_mgr.set_secret("restricted/secret", "value")

        assert "restricted/secret" in str(exc_info.value.secret_name)
        assert "permission denied" in str(exc_info.value).lower()

    def test_set_secret_invalid_request(self, vault_secret_mgr, mock_hvac_client):
        """Test setting secret with invalid request."""
        mock_hvac_client.secrets.kv.v2.create_or_update_secret.side_effect = InvalidRequest(
            "Invalid request"
        )

        with pytest.raises(SecretWriteError) as exc_info:
            vault_secret_mgr.set_secret("database/password", "value")

        assert "database/password" in str(exc_info.value.secret_name)

    def test_set_secret_vault_down(self, vault_secret_mgr, mock_hvac_client):
        """Test setting secret when Vault is down."""
        mock_hvac_client.secrets.kv.v2.create_or_update_secret.side_effect = VaultDown(
            "Vault unreachable"
        )

        with pytest.raises(SecretAccessError) as exc_info:
            vault_secret_mgr.set_secret("database/password", "value")

        assert "unreachable" in str(exc_info.value).lower()

    def test_set_secret_vault_error(self, vault_secret_mgr, mock_hvac_client):
        """Test setting secret when Vault returns an error."""
        mock_hvac_client.secrets.kv.v2.create_or_update_secret.side_effect = VaultError(
            "Internal error"
        )

        with pytest.raises(SecretWriteError) as exc_info:
            vault_secret_mgr.set_secret("database/password", "value")

        assert "database/password" in str(exc_info.value.secret_name)


# ================================================================================
# Test close
# ================================================================================


class TestVaultSecretManagerClose:
    """Test VaultSecretManager.close() method."""

    def test_close_clears_cache(self, vault_secret_mgr, mock_hvac_client):
        """Test close() clears the in-memory cache."""
        # Populate cache
        mock_hvac_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"value": "value1"}}
        }
        vault_secret_mgr.get_secret("secret1")

        assert len(vault_secret_mgr._cache) == 1  # Cache populated

        # Close should clear cache
        vault_secret_mgr.close()

        assert len(vault_secret_mgr._cache) == 0

    def test_close_closes_http_adapter(self, vault_secret_mgr, mock_hvac_client):
        """Test close() closes the hvac client's HTTP adapter."""
        mock_adapter = Mock()
        mock_hvac_client.adapter = mock_adapter

        vault_secret_mgr.close()

        mock_adapter.close.assert_called_once()

    def test_close_without_adapter(self, vault_secret_mgr, mock_hvac_client):
        """Test close() handles missing adapter gracefully."""
        # Remove adapter attribute
        delattr(mock_hvac_client, "adapter")

        # Should not raise exception
        vault_secret_mgr.close()


# ================================================================================
# Test Context Manager
# ================================================================================


class TestVaultSecretManagerContextManager:
    """Test VaultSecretManager context manager protocol."""

    def test_context_manager_enter_exit(self, mock_hvac_client):
        """Test using VaultSecretManager as context manager."""
        with patch("libs.secrets.vault_backend.hvac.Client", return_value=mock_hvac_client):
            with VaultSecretManager(
                vault_url="https://vault.example.com:8200",
                token="s.test_token",
            ) as secret_mgr:
                assert secret_mgr._client == mock_hvac_client

        # Cache should be cleared after exiting context
        assert len(secret_mgr._cache) == 0

    def test_context_manager_with_operations(self, mock_hvac_client):
        """Test context manager with secret operations."""
        mock_hvac_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"value": "test_value"}}
        }

        with patch("libs.secrets.vault_backend.hvac.Client", return_value=mock_hvac_client):
            with VaultSecretManager(
                vault_url="https://vault.example.com:8200",
                token="s.test_token",
            ) as secret_mgr:
                value = secret_mgr.get_secret("test_secret")
                assert value == "test_value"

        # Cache cleared after exit
        assert len(secret_mgr._cache) == 0

    def test_context_manager_exception_handling(self, mock_hvac_client):
        """Test context manager properly closes on exception."""
        with patch("libs.secrets.vault_backend.hvac.Client", return_value=mock_hvac_client):
            with pytest.raises(ValueError, match="Test exception"):  # noqa: PT012 - Complex test intentionally tests cleanup on exception
                with VaultSecretManager(
                    vault_url="https://vault.example.com:8200",
                    token="s.test_token",
                ) as secret_mgr:
                    # Populate cache
                    mock_hvac_client.secrets.kv.v2.read_secret_version.return_value = {
                        "data": {"data": {"value": "test_value"}}
                    }
                    secret_mgr.get_secret("test_secret")
                    raise ValueError("Test exception")

            # Cache should still be cleared despite exception
            assert len(secret_mgr._cache) == 0


# ================================================================================
# Test Caching
# ================================================================================


class TestVaultSecretManagerCaching:
    """Test VaultSecretManager caching behavior."""

    def test_cache_hit_after_first_access(self, vault_secret_mgr, mock_hvac_client):
        """Test cache hit after initial secret fetch."""
        mock_hvac_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"value": "cached_value"}}
        }

        # First access - cache miss
        value1 = vault_secret_mgr.get_secret("database/password")
        assert value1 == "cached_value"

        # Reset mock to verify no additional calls
        mock_hvac_client.secrets.kv.v2.read_secret_version.reset_mock()

        # Second access - should hit cache
        value2 = vault_secret_mgr.get_secret("database/password")
        assert value2 == "cached_value"

        # Should not call Vault again
        mock_hvac_client.secrets.kv.v2.read_secret_version.assert_not_called()

    def test_cache_expiration(self, mock_hvac_client):
        """Test cache expiration after TTL."""
        with patch("libs.secrets.vault_backend.hvac.Client", return_value=mock_hvac_client):
            secret_mgr = VaultSecretManager(
                vault_url="https://vault.example.com:8200",
                token="s.test_token",
                cache_ttl_seconds=1,  # 1 second TTL
            )

        mock_hvac_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"value": "cached_value"}}
        }

        # First access
        value1 = secret_mgr.get_secret("database/password")
        assert value1 == "cached_value"

        # Wait for cache to expire
        time.sleep(1.1)

        # Second access should fetch from Vault (cache expired)
        mock_hvac_client.secrets.kv.v2.read_secret_version.reset_mock()
        value2 = secret_mgr.get_secret("database/password")
        assert value2 == "cached_value"

        # Should have called Vault again
        mock_hvac_client.secrets.kv.v2.read_secret_version.assert_called_once()

    def test_cache_invalidation_on_set(self, vault_secret_mgr, mock_hvac_client):
        """Test cache invalidation after set_secret()."""
        # First get the secret (populate cache)
        mock_hvac_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"value": "old_value"}}
        }
        value1 = vault_secret_mgr.get_secret("database/password")
        assert value1 == "old_value"
        assert len(vault_secret_mgr._cache) == 1  # Cache populated

        # Update the secret (should invalidate cache)
        vault_secret_mgr.set_secret("database/password", "new_value")

        # Cache should be empty (invalidated)
        assert len(vault_secret_mgr._cache) == 0

        # Next get should fetch from Vault
        mock_hvac_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"value": "new_value"}}
        }
        value2 = vault_secret_mgr.get_secret("database/password")
        assert value2 == "new_value"

    def test_cache_multiple_secrets(self, vault_secret_mgr, mock_hvac_client):
        """Test caching multiple different secrets."""
        # Add multiple secrets to cache
        mock_hvac_client.secrets.kv.v2.read_secret_version.side_effect = [
            {"data": {"data": {"value": "value1"}}},
            {"data": {"data": {"value": "value2"}}},
            {"data": {"data": {"value": "value3"}}},
        ]

        vault_secret_mgr.get_secret("secret1")
        vault_secret_mgr.get_secret("secret2")
        vault_secret_mgr.get_secret("secret3")

        # Verify all 3 secrets cached
        assert len(vault_secret_mgr._cache) == 3

    def test_cache_disabled(self, mock_hvac_client):
        """Test behavior when caching is disabled (TTL=0)."""
        with patch("libs.secrets.vault_backend.hvac.Client", return_value=mock_hvac_client):
            secret_mgr = VaultSecretManager(
                vault_url="https://vault.example.com:8200",
                token="s.test_token",
                cache_ttl_seconds=0,  # Disable caching
            )

        mock_hvac_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"value": "value"}}
        }

        # First access
        secret_mgr.get_secret("database/password")

        # Cache should be populated (SecretCache still caches, TTL check happens on get)
        assert len(secret_mgr._cache) == 1

        # Second access should fetch from Vault (cache expired immediately with TTL=0)
        mock_hvac_client.secrets.kv.v2.read_secret_version.reset_mock()
        secret_mgr.get_secret("database/password")

        # Should have called Vault again (cache expired)
        mock_hvac_client.secrets.kv.v2.read_secret_version.assert_called_once()

    def test_cache_stores_timestamp(self, vault_secret_mgr, mock_hvac_client):
        """Test cache stores values correctly (timestamps handled by SecretCache)."""
        mock_hvac_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"value": "cached_value"}}
        }

        # Get secret (should cache)
        result = vault_secret_mgr.get_secret("database/password")
        assert result == "cached_value"

        # Verify cache contains entry
        assert len(vault_secret_mgr._cache) == 1

        # Second call should use cache (no additional Vault call)
        mock_hvac_client.secrets.kv.v2.read_secret_version.reset_mock()
        result2 = vault_secret_mgr.get_secret("database/password")
        assert result2 == "cached_value"
        mock_hvac_client.secrets.kv.v2.read_secret_version.assert_not_called()


# ================================================================================
# Test Thread Safety
# ================================================================================


class TestVaultSecretManagerThreadSafety:
    """Test VaultSecretManager thread safety."""

    def test_concurrent_get_secret(self, vault_secret_mgr, mock_hvac_client):
        """Test concurrent get_secret calls are thread-safe."""
        mock_hvac_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"value": "value1"}}
        }
        results = []

        def get_secret_worker():
            value = vault_secret_mgr.get_secret("database/password")
            results.append(value)

        threads = [threading.Thread(target=get_secret_worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All threads should get the same value
        assert len(results) == 10
        assert all(r == "value1" for r in results)

    def test_concurrent_set_secret(self, vault_secret_mgr, mock_hvac_client):
        """Test concurrent set_secret calls are thread-safe."""
        set_count = [0]
        lock = threading.Lock()

        def set_secret_worker(value):
            vault_secret_mgr.set_secret("database/password", value)
            with lock:
                set_count[0] += 1

        threads = [
            threading.Thread(target=set_secret_worker, args=(f"value{i}",)) for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All writes should have completed
        assert set_count[0] == 10

    def test_concurrent_get_and_set(self, vault_secret_mgr, mock_hvac_client):
        """Test concurrent get and set operations are thread-safe."""
        mock_hvac_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"value": "initial_value"}}
        }
        results = []
        lock = threading.Lock()

        def get_worker():
            value = vault_secret_mgr.get_secret("database/password")
            with lock:
                results.append(("get", value))

        def set_worker(value):
            vault_secret_mgr.set_secret("database/password", value)
            with lock:
                results.append(("set", value))

        # Mix of get and set operations
        threads = []
        for i in range(5):
            threads.append(threading.Thread(target=get_worker))
            threads.append(threading.Thread(target=set_worker, args=(f"value{i}",)))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All operations should have completed
        assert len(results) == 10

    def test_concurrent_cache_access(self, vault_secret_mgr, mock_hvac_client):
        """Test concurrent cache access is thread-safe."""
        mock_hvac_client.secrets.kv.v2.read_secret_version.return_value = {
            "data": {"data": {"value": "cached_value"}}
        }

        # Populate cache first
        vault_secret_mgr.get_secret("database/password")

        results = []

        def cache_access_worker():
            # Access cached value (should not call Vault)
            value = vault_secret_mgr.get_secret("database/password")
            results.append(value)

        threads = [threading.Thread(target=cache_access_worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All threads should get cached value
        assert len(results) == 20
        assert all(r == "cached_value" for r in results)


# ================================================================================
# Test Error Handling
# ================================================================================


class TestVaultSecretManagerErrorHandling:
    """Test VaultSecretManager error handling."""

    def test_unexpected_exception_on_get(self, vault_secret_mgr, mock_hvac_client):
        """Test handling of unexpected exception during get_secret."""
        mock_hvac_client.secrets.kv.v2.read_secret_version.side_effect = RuntimeError(
            "Unexpected error"
        )

        with pytest.raises(SecretAccessError) as exc_info:
            vault_secret_mgr.get_secret("database/password")

        assert "unexpected error" in str(exc_info.value).lower()

    def test_unexpected_exception_on_list(self, vault_secret_mgr, mock_hvac_client):
        """Test handling of unexpected exception during list_secrets."""
        mock_hvac_client.secrets.kv.v2.list_secrets.side_effect = RuntimeError(
            "Unexpected error"
        )

        with pytest.raises(SecretAccessError) as exc_info:
            vault_secret_mgr.list_secrets()

        assert "unexpected error" in str(exc_info.value).lower()

    def test_unexpected_exception_on_set(self, vault_secret_mgr, mock_hvac_client):
        """Test handling of unexpected exception during set_secret."""
        mock_hvac_client.secrets.kv.v2.create_or_update_secret.side_effect = RuntimeError(
            "Unexpected error"
        )

        with pytest.raises(SecretWriteError) as exc_info:
            vault_secret_mgr.set_secret("database/password", "value")

        assert "unexpected error" in str(exc_info.value).lower()

    def test_set_secret_retries_on_vault_down(self, vault_secret_mgr, mock_hvac_client):
        """Ensure set_secret retries three times on VaultDown."""
        mock_hvac_client.secrets.kv.v2.create_or_update_secret.side_effect = VaultDown(
            "Vault unreachable"
        )

        with pytest.raises(SecretAccessError):
            vault_secret_mgr.set_secret("database/password", "value")

        assert mock_hvac_client.secrets.kv.v2.create_or_update_secret.call_count == 3
