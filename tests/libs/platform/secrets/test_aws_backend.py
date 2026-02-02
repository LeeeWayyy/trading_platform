"""
Test Suite for AWSSecretsManager (libs/secrets/aws_backend.py).

This module tests the AWS Secrets Manager backend implementation, verifying:
- Initialization (IAM role auth, access key auth, invalid region)
- Secret retrieval (get_secret with cache, retries, error handling)
- Secret listing (list_secrets with prefix filter, pagination)
- Secret writes (set_secret create/update, error handling)
- Cache behavior (TTL, invalidation, expiry)
- Context manager (__enter__, __exit__)
- Retry logic (exponential backoff for transient failures)
- AWS-specific error scenarios (ResourceNotFound, AccessDenied, etc.)

Test Coverage:
    - Unit tests for all public methods
    - Mock AWS Secrets Manager API responses via unittest.mock
    - Edge cases: empty lists, cache expiry, binary secrets, pagination
    - Error conditions: auth failures, network errors, API throttling
    - Thread safety verification

See also:
    - libs/secrets/aws_backend.py - Implementation under test
    - libs/secrets/manager.py - SecretManager interface contract
    - docs/ADRs/0017-secrets-management.md - Architecture decisions
"""

import threading
import time
from unittest.mock import MagicMock, Mock, patch

import pytest
from botocore.exceptions import ClientError

from libs.platform.secrets.aws_backend import AWSSecretsManager
from libs.platform.secrets.exceptions import (
    SecretAccessError,
    SecretNotFoundError,
    SecretWriteError,
)


class TestAWSSecretsManagerInitialization:
    """Test suite for AWSSecretsManager initialization and authentication."""

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_init_success_iam_role(self, mock_boto_client: Mock) -> None:
        """Test successful initialization with IAM role authentication (no explicit credentials)."""
        # Arrange
        mock_client = MagicMock()
        mock_boto_client.return_value = mock_client

        # Act
        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Assert
        assert secret_mgr._region_name == "us-east-1"
        assert secret_mgr._cache is not None  # SecretCache instance
        assert len(secret_mgr._cache) == 0  # Empty cache
        mock_boto_client.assert_called_once_with(
            "secretsmanager",
            region_name="us-east-1",
        )
        # Note: list_secrets() no longer called during __init__ to support read-only roles

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_init_success_access_keys(self, mock_boto_client: Mock) -> None:
        """Test successful initialization with access key authentication."""
        # Arrange
        mock_client = MagicMock()
        mock_client.list_secrets.return_value = {"SecretList": []}
        mock_boto_client.return_value = mock_client

        # Act
        secret_mgr = AWSSecretsManager(
            region_name="us-west-2",
            aws_access_key_id="AKIAIOSFODNN7EXAMPLE",
            aws_secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        )

        # Assert
        assert secret_mgr._region_name == "us-west-2"
        mock_boto_client.assert_called_once_with(
            "secretsmanager",
            region_name="us-west-2",
            aws_access_key_id="AKIAIOSFODNN7EXAMPLE",
            aws_secret_access_key="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        )

    # Note: test_init_auth_failure and test_init_network_error removed
    # Rationale: __init__ no longer calls list_secrets() to support read-only IAM roles
    # Validation now happens lazily on first get_secret() call
    # Auth/network failure tests covered by get_secret test suite


class TestAWSSecretsManagerGetSecret:
    """Test suite for get_secret() method."""

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_get_secret_success(self, mock_boto_client: Mock) -> None:
        """Test successful secret retrieval (cache miss → AWS API call)."""
        # Arrange
        mock_client = MagicMock()
        mock_client.list_secrets.return_value = {"SecretList": []}
        mock_client.get_secret_value.return_value = {
            "SecretString": "my_password_123",
        }
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Act
        value = secret_mgr.get_secret("prod/database/password")

        # Assert
        assert value == "my_password_123"
        mock_client.get_secret_value.assert_called_once_with(SecretId="prod/database/password")
        # Verify caching (cache has 1 entry)
        assert len(secret_mgr._cache) == 1

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_get_secret_cache_hit(self, mock_boto_client: Mock) -> None:
        """Test cache hit (no AWS API call on second retrieval)."""
        # Arrange
        mock_client = MagicMock()
        mock_client.list_secrets.return_value = {"SecretList": []}
        mock_client.get_secret_value.return_value = {
            "SecretString": "cached_value",
        }
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Act
        value1 = secret_mgr.get_secret("test/secret")
        value2 = secret_mgr.get_secret("test/secret")  # Cache hit

        # Assert
        assert value1 == "cached_value"
        assert value2 == "cached_value"
        # AWS API called only once (second call uses cache)
        assert mock_client.get_secret_value.call_count == 1

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_get_secret_cache_expired(self, mock_boto_client: Mock) -> None:
        """Test cache expiry (AWS API call after TTL)."""
        # Arrange
        mock_client = MagicMock()
        mock_client.list_secrets.return_value = {"SecretList": []}
        mock_client.get_secret_value.return_value = {
            "SecretString": "refreshed_value",
        }
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(
            region_name="us-east-1",
            cache_ttl_seconds=1,  # 1 second TTL
        )

        # Act
        secret_mgr.get_secret("test/secret")  # Cache miss
        assert len(secret_mgr._cache) == 1  # Cached

        # Wait for cache to expire
        time.sleep(1.1)  # TTL=1 second, wait 1.1 seconds

        secret_mgr.get_secret("test/secret")  # Cache expired, fetch again

        # Assert - should have called AWS twice (initial + after expiry)
        assert mock_client.get_secret_value.call_count == 2

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_get_secret_not_found(self, mock_boto_client: Mock) -> None:
        """Test SecretNotFoundError when secret doesn't exist."""
        # Arrange
        mock_client = MagicMock()
        mock_client.list_secrets.return_value = {"SecretList": []}
        mock_client.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}},
            "GetSecretValue",
        )
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Act & Assert
        with pytest.raises(SecretNotFoundError) as exc_info:
            secret_mgr.get_secret("nonexistent/secret")

        assert "nonexistent/secret" in str(exc_info.value)
        assert "not found" in str(exc_info.value)

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_get_secret_access_denied(self, mock_boto_client: Mock) -> None:
        """Test SecretAccessError when IAM permissions insufficient."""
        # Arrange
        mock_client = MagicMock()
        mock_client.list_secrets.return_value = {"SecretList": []}
        mock_client.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException"}},
            "GetSecretValue",
        )
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Act & Assert
        with pytest.raises(SecretAccessError) as exc_info:
            secret_mgr.get_secret("protected/secret")

        assert "Access denied" in str(exc_info.value)
        assert "secretsmanager:GetSecretValue" in str(exc_info.value)

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_get_secret_binary_format_error(self, mock_boto_client: Mock) -> None:
        """Test error when secret is binary (not supported)."""
        # Arrange
        mock_client = MagicMock()
        mock_client.list_secrets.return_value = {"SecretList": []}
        mock_client.get_secret_value.return_value = {
            "SecretBinary": b"binary_data",
            # No SecretString field
        }
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Act & Assert
        with pytest.raises(SecretAccessError) as exc_info:
            secret_mgr.get_secret("binary/secret")

        assert "Secret is binary" in str(exc_info.value)

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_get_secret_invalid_request(self, mock_boto_client: Mock) -> None:
        """Test SecretAccessError when secret marked for deletion."""
        # Arrange
        mock_client = MagicMock()
        mock_client.list_secrets.return_value = {"SecretList": []}
        mock_client.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "InvalidRequestException"}},
            "GetSecretValue",
        )
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Act & Assert
        with pytest.raises(SecretAccessError) as exc_info:
            secret_mgr.get_secret("deleted/secret")

        assert "marked for deletion" in str(exc_info.value)


class TestAWSSecretsManagerListSecrets:
    """Test suite for list_secrets() method."""

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_list_secrets_all(self, mock_boto_client: Mock) -> None:
        """Test listing all secrets (no prefix filter)."""
        # Arrange
        mock_client = MagicMock()
        mock_client.list_secrets.return_value = {"SecretList": []}

        # Mock paginator
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [
            {
                "SecretList": [
                    {"Name": "prod/database/password"},
                    {"Name": "prod/alpaca/api_key_id"},
                    {"Name": "staging/test_secret"},
                ]
            }
        ]
        mock_client.get_paginator.return_value = mock_paginator
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Act
        secrets = secret_mgr.list_secrets()

        # Assert
        assert len(secrets) == 3
        assert "prod/database/password" in secrets
        assert "prod/alpaca/api_key_id" in secrets
        assert "staging/test_secret" in secrets

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_list_secrets_with_prefix(self, mock_boto_client: Mock) -> None:
        """Test listing secrets with prefix filter."""
        # Arrange
        mock_client = MagicMock()
        mock_client.list_secrets.return_value = {"SecretList": []}

        # Mock paginator
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [
            {
                "SecretList": [
                    {"Name": "prod/database/password"},
                    {"Name": "prod/alpaca/api_key_id"},
                    {"Name": "staging/test_secret"},
                ]
            }
        ]
        mock_client.get_paginator.return_value = mock_paginator
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Act
        secrets = secret_mgr.list_secrets(prefix="prod/")

        # Assert
        assert len(secrets) == 2
        assert "prod/database/password" in secrets
        assert "prod/alpaca/api_key_id" in secrets
        assert "staging/test_secret" not in secrets

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_list_secrets_empty(self, mock_boto_client: Mock) -> None:
        """Test listing secrets when no secrets exist."""
        # Arrange
        mock_client = MagicMock()
        mock_client.list_secrets.return_value = {"SecretList": []}

        # Mock paginator
        mock_paginator = MagicMock()
        mock_paginator.paginate.return_value = [{"SecretList": []}]
        mock_client.get_paginator.return_value = mock_paginator
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Act
        secrets = secret_mgr.list_secrets()

        # Assert
        assert secrets == []

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_list_secrets_access_denied(self, mock_boto_client: Mock) -> None:
        """Test SecretAccessError when IAM permissions insufficient for listing."""
        # Arrange
        mock_client = MagicMock()
        mock_client.list_secrets.return_value = {"SecretList": []}

        # Mock paginator
        mock_paginator = MagicMock()
        mock_paginator.paginate.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException"}},
            "ListSecrets",
        )
        mock_client.get_paginator.return_value = mock_paginator
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Act & Assert
        with pytest.raises(SecretAccessError) as exc_info:
            secret_mgr.list_secrets()

        assert "Access denied" in str(exc_info.value)
        assert "secretsmanager:ListSecrets" in str(exc_info.value)


class TestAWSSecretsManagerSetSecret:
    """Test suite for set_secret() method."""

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_set_secret_update_existing(self, mock_boto_client: Mock) -> None:
        """Test updating an existing secret (PutSecretValue)."""
        # Arrange
        mock_client = MagicMock()
        mock_client.list_secrets.return_value = {"SecretList": []}
        mock_client.put_secret_value.return_value = {"VersionId": "v1"}
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Act
        secret_mgr.set_secret("prod/database/password", "new_password_123")

        # Assert
        mock_client.put_secret_value.assert_called_once_with(
            SecretId="prod/database/password",
            SecretString="new_password_123",
        )

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_set_secret_create_new(self, mock_boto_client: Mock) -> None:
        """Test creating a new secret (CreateSecret when ResourceNotFound)."""
        # Arrange
        mock_client = MagicMock()
        mock_client.list_secrets.return_value = {"SecretList": []}
        mock_client.put_secret_value.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}},
            "PutSecretValue",
        )
        mock_client.create_secret.return_value = {"ARN": "arn:aws:..."}
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Act
        secret_mgr.set_secret("new/secret", "secret_value")

        # Assert
        mock_client.put_secret_value.assert_called_once()
        mock_client.create_secret.assert_called_once_with(
            Name="new/secret",
            SecretString="secret_value",
        )

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_set_secret_cache_invalidation(self, mock_boto_client: Mock) -> None:
        """Test cache invalidation after set_secret()."""
        # Arrange
        mock_client = MagicMock()
        mock_client.list_secrets.return_value = {"SecretList": []}
        mock_client.get_secret_value.return_value = {
            "SecretString": "old_value",
        }
        mock_client.put_secret_value.return_value = {"VersionId": "v2"}
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Act
        secret_mgr.get_secret("test/secret")  # Cache "old_value"
        assert len(secret_mgr._cache) == 1  # Cached

        secret_mgr.set_secret("test/secret", "new_value")  # Invalidate cache

        # Assert - cache should be empty (invalidated)
        assert len(secret_mgr._cache) == 0

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_set_secret_access_denied(self, mock_boto_client: Mock) -> None:
        """Test SecretWriteError when IAM permissions insufficient."""
        # Arrange
        mock_client = MagicMock()
        mock_client.list_secrets.return_value = {"SecretList": []}
        mock_client.put_secret_value.side_effect = ClientError(
            {"Error": {"Code": "AccessDeniedException"}},
            "PutSecretValue",
        )
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Act & Assert
        with pytest.raises(SecretWriteError) as exc_info:
            secret_mgr.set_secret("protected/secret", "value")

        assert "Access denied" in str(exc_info.value)
        assert "secretsmanager:PutSecretValue" in str(exc_info.value)

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_set_secret_marked_for_deletion(self, mock_boto_client: Mock) -> None:
        """Test SecretWriteError when secret marked for deletion."""
        # Arrange
        mock_client = MagicMock()
        mock_client.list_secrets.return_value = {"SecretList": []}
        mock_client.put_secret_value.side_effect = ClientError(
            {"Error": {"Code": "InvalidRequestException"}},
            "PutSecretValue",
        )
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Act & Assert
        with pytest.raises(SecretWriteError) as exc_info:
            secret_mgr.set_secret("deleted/secret", "value")

        assert "marked for deletion" in str(exc_info.value)


class TestAWSSecretsManagerClose:
    """Test suite for close() and context manager."""

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_close_clears_cache(self, mock_boto_client: Mock) -> None:
        """Test close() clears in-memory cache."""
        # Arrange
        mock_client = MagicMock()
        mock_client.list_secrets.return_value = {"SecretList": []}
        mock_client.get_secret_value.return_value = {
            "SecretString": "cached_value",
        }
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")
        secret_mgr.get_secret("test/secret")  # Populate cache
        assert len(secret_mgr._cache) == 1  # Cached

        # Act
        secret_mgr.close()

        # Assert - cache should be cleared
        assert len(secret_mgr._cache) == 0

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_context_manager(self, mock_boto_client: Mock) -> None:
        """Test context manager calls close() on exit."""
        # Arrange
        mock_client = MagicMock()
        mock_client.list_secrets.return_value = {"SecretList": []}
        mock_client.get_secret_value.return_value = {
            "SecretString": "value",
        }
        mock_boto_client.return_value = mock_client

        # Act
        with AWSSecretsManager(region_name="us-east-1") as secret_mgr:
            secret_mgr.get_secret("test/secret")
            assert len(secret_mgr._cache) == 1  # Cached

        # Assert (context manager called close())
        assert len(secret_mgr._cache) == 0  # Cache cleared


# ================================================================================
# Retry Logic Tests
# ================================================================================


class TestAWSSecretsManagerRetryLogic:
    """Test suite for retry logic on transient vs. permanent errors."""

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_get_secret_retries_on_transient_error(self, mock_boto_client: Mock) -> None:
        """Test get_secret retries on transient errors (e.g., ThrottlingException)."""
        # Arrange
        mock_client = MagicMock()
        throttling_error = ClientError({"Error": {"Code": "ThrottlingException"}}, "GetSecretValue")
        mock_client.get_secret_value.side_effect = [
            throttling_error,
            {"SecretString": "successful_value"},
        ]
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Act
        value = secret_mgr.get_secret("test/secret")

        # Assert
        assert value == "successful_value"
        # Should be called twice: once for the failure, once for the success
        assert mock_client.get_secret_value.call_count == 2

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_get_secret_does_not_retry_on_permanent_error(self, mock_boto_client: Mock) -> None:
        """Test get_secret does NOT retry on permanent errors (e.g., AccessDeniedException)."""
        # Arrange
        mock_client = MagicMock()
        access_denied_error = ClientError(
            {"Error": {"Code": "AccessDeniedException"}}, "GetSecretValue"
        )
        mock_client.get_secret_value.side_effect = access_denied_error
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Act & Assert
        with pytest.raises(SecretAccessError):
            secret_mgr.get_secret("test/secret")

        # Should be called only once, as permanent errors are not retried
        assert mock_client.get_secret_value.call_count == 1

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_get_secret_exhausts_retries_raises_original_exception(
        self, mock_boto_client: Mock
    ) -> None:
        """Test get_secret raises original exception (not RetryError) when retries exhausted."""
        # Arrange
        mock_client = MagicMock()
        throttling_error = ClientError({"Error": {"Code": "ThrottlingException"}}, "GetSecretValue")
        # Always fail with transient error to exhaust retries
        mock_client.get_secret_value.side_effect = throttling_error
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Act & Assert
        # Should raise SecretAccessError (NOT RetryError)
        with pytest.raises(SecretAccessError) as exc_info:
            secret_mgr.get_secret("test/secret")

        # Verify it's the expected domain exception with context
        assert "AWS API error" in str(exc_info.value)
        assert "ThrottlingException" in str(exc_info.value)

        # Should be called 3 times (initial + 2 retries)
        assert mock_client.get_secret_value.call_count == 3

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_list_secrets_exhausts_retries_raises_original_exception(
        self, mock_boto_client: Mock
    ) -> None:
        """Test list_secrets raises original exception (not RetryError) when retries exhausted."""
        # Arrange
        mock_client = MagicMock()
        throttling_error = ClientError({"Error": {"Code": "ServiceUnavailable"}}, "ListSecrets")

        # Mock paginator to always fail with transient error
        mock_paginator = MagicMock()
        mock_paginator.paginate.side_effect = throttling_error
        mock_client.get_paginator.return_value = mock_paginator
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Act & Assert
        # Should raise SecretAccessError (NOT RetryError)
        with pytest.raises(SecretAccessError) as exc_info:
            secret_mgr.list_secrets()

        # Verify it's the expected domain exception with context
        assert "AWS API error" in str(exc_info.value)
        assert "ServiceUnavailable" in str(exc_info.value)

        # Should be called 3 times (initial + 2 retries)
        assert mock_paginator.paginate.call_count == 3


# ================================================================================
# Thread Safety Tests
# ================================================================================


class TestAWSSecretsManagerThreadSafety:
    """Test AWSSecretsManager thread safety."""

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_concurrent_get_secret(self, mock_boto_client: Mock) -> None:
        """Test concurrent get_secret calls are thread-safe."""
        # Arrange
        mock_client = MagicMock()
        mock_client.get_secret_value.return_value = {
            "SecretString": "concurrent_value",
        }
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")
        results: list[str] = []

        def get_secret_worker() -> None:
            value = secret_mgr.get_secret("test/secret")
            results.append(value)

        # Act - 10 concurrent get_secret calls
        threads = [threading.Thread(target=get_secret_worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert - All threads should get the same value
        assert len(results) == 10
        assert all(r == "concurrent_value" for r in results)

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_concurrent_set_secret(self, mock_boto_client: Mock) -> None:
        """Test concurrent set_secret calls are thread-safe."""
        # Arrange
        mock_client = MagicMock()
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")
        set_count = [0]
        lock = threading.Lock()

        def set_secret_worker(value: str) -> None:
            secret_mgr.set_secret("test/secret", value)
            with lock:
                set_count[0] += 1

        # Act - 10 concurrent set_secret calls
        threads = [
            threading.Thread(target=set_secret_worker, args=(f"value{i}",)) for i in range(10)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert - All writes should have completed
        assert set_count[0] == 10

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_concurrent_get_and_set(self, mock_boto_client: Mock) -> None:
        """Test concurrent get and set operations are thread-safe."""
        # Arrange
        mock_client = MagicMock()
        mock_client.get_secret_value.return_value = {
            "SecretString": "initial_value",
        }
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")
        results: list[tuple[str, str]] = []
        lock = threading.Lock()

        def get_worker() -> None:
            value = secret_mgr.get_secret("test/secret")
            with lock:
                results.append(("get", value))

        def set_worker(value: str) -> None:
            secret_mgr.set_secret("test/secret", value)
            with lock:
                results.append(("set", value))

        # Act - Mix of get and set operations
        threads = []
        for i in range(5):
            threads.append(threading.Thread(target=get_worker))
            threads.append(threading.Thread(target=set_worker, args=(f"value{i}",)))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert - All operations should have completed
        assert len(results) == 10

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_concurrent_cache_access(self, mock_boto_client: Mock) -> None:
        """Test concurrent cache access is thread-safe."""
        # Arrange
        mock_client = MagicMock()
        mock_client.get_secret_value.return_value = {
            "SecretString": "cached_value",
        }
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Populate cache first
        secret_mgr.get_secret("test/secret")
        results: list[str] = []

        def cache_access_worker() -> None:
            # Access cached value (should not call AWS)
            value = secret_mgr.get_secret("test/secret")
            results.append(value)

        # Act - 20 concurrent cache accesses
        threads = [threading.Thread(target=cache_access_worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # Assert - All threads should get cached value
        assert len(results) == 20
        assert all(r == "cached_value" for r in results)
        # AWS should have been called only once (during cache population)
        assert mock_client.get_secret_value.call_count == 1


# ================================================================================
# Additional Error Handling Tests (Missing Coverage)
# ================================================================================


class TestAWSSecretsManagerInitializationErrors:
    """Test suite for initialization error paths."""

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_init_client_error(self, mock_boto_client: Mock) -> None:
        """Test initialization fails with ClientError (e.g., invalid credentials)."""
        # Arrange
        mock_boto_client.side_effect = ClientError(
            {"Error": {"Code": "InvalidClientTokenId"}},
            "CreateClient",
        )

        # Act & Assert
        with pytest.raises(SecretAccessError) as exc_info:
            AWSSecretsManager(region_name="us-east-1")

        assert "AWS authentication failed" in str(exc_info.value)
        assert "InvalidClientTokenId" in str(exc_info.value)

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_init_botocore_error(self, mock_boto_client: Mock) -> None:
        """Test initialization fails with BotoCoreError (e.g., network error)."""
        # Arrange
        from botocore.exceptions import EndpointConnectionError

        mock_boto_client.side_effect = EndpointConnectionError(
            endpoint_url="https://secretsmanager.us-east-1.amazonaws.com"
        )

        # Act & Assert
        with pytest.raises(SecretAccessError) as exc_info:
            AWSSecretsManager(region_name="us-east-1")

        assert "AWS SDK error during initialization" in str(exc_info.value)

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_init_value_error(self, mock_boto_client: Mock) -> None:
        """Test initialization fails with ValueError (e.g., invalid region format)."""
        # Arrange
        mock_boto_client.side_effect = ValueError("Invalid region name")

        # Act & Assert
        with pytest.raises(SecretAccessError) as exc_info:
            AWSSecretsManager(region_name="invalid-region!")

        assert "Invalid AWS configuration" in str(exc_info.value)


class TestAWSSecretsManagerGetSecretAdditionalErrors:
    """Test suite for additional get_secret error paths."""

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_get_secret_generic_client_error(self, mock_boto_client: Mock) -> None:
        """Test get_secret with generic ClientError (not specifically handled)."""
        # Arrange
        mock_client = MagicMock()
        mock_client.get_secret_value.side_effect = ClientError(
            {"Error": {"Code": "InternalFailure"}},
            "GetSecretValue",
        )
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Act & Assert
        with pytest.raises(SecretAccessError) as exc_info:
            secret_mgr.get_secret("test/secret")

        assert "AWS API error" in str(exc_info.value)
        assert "InternalFailure" in str(exc_info.value)

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_get_secret_botocore_error(self, mock_boto_client: Mock) -> None:
        """Test get_secret with BotoCoreError (network timeout)."""
        # Arrange
        from botocore.exceptions import ReadTimeoutError

        mock_client = MagicMock()
        mock_client.get_secret_value.side_effect = ReadTimeoutError(
            endpoint_url="https://secretsmanager.us-east-1.amazonaws.com"
        )
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Act & Assert
        with pytest.raises(SecretAccessError) as exc_info:
            secret_mgr.get_secret("test/secret")

        assert "AWS SDK error retrieving secret" in str(exc_info.value)


class TestAWSSecretsManagerListSecretsAdditionalErrors:
    """Test suite for additional list_secrets error paths."""

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_list_secrets_generic_client_error(self, mock_boto_client: Mock) -> None:
        """Test list_secrets with generic ClientError."""
        # Arrange
        mock_client = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.side_effect = ClientError(
            {"Error": {"Code": "InternalServiceError"}},
            "ListSecrets",
        )
        mock_client.get_paginator.return_value = mock_paginator
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Act & Assert
        with pytest.raises(SecretAccessError) as exc_info:
            secret_mgr.list_secrets()

        assert "AWS API error" in str(exc_info.value)
        assert "InternalServiceError" in str(exc_info.value)

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_list_secrets_botocore_error(self, mock_boto_client: Mock) -> None:
        """Test list_secrets with BotoCoreError."""
        # Arrange
        from botocore.exceptions import ConnectionError as BotocoreConnectionError

        mock_client = MagicMock()
        mock_paginator = MagicMock()
        mock_paginator.paginate.side_effect = BotocoreConnectionError(error="Connection refused")
        mock_client.get_paginator.return_value = mock_paginator
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Act & Assert
        with pytest.raises(SecretAccessError) as exc_info:
            secret_mgr.list_secrets()

        assert "AWS SDK error listing secrets" in str(exc_info.value)


class TestAWSSecretsManagerSetSecretAdditionalErrors:
    """Test suite for additional set_secret error paths."""

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_set_secret_generic_client_error(self, mock_boto_client: Mock) -> None:
        """Test set_secret with generic ClientError."""
        # Arrange
        mock_client = MagicMock()
        mock_client.put_secret_value.side_effect = ClientError(
            {"Error": {"Code": "LimitExceededException"}},
            "PutSecretValue",
        )
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Act & Assert
        with pytest.raises(SecretWriteError) as exc_info:
            secret_mgr.set_secret("test/secret", "value")

        assert "AWS error writing secret" in str(exc_info.value)
        assert "LimitExceededException" in str(exc_info.value)

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_set_secret_botocore_error(self, mock_boto_client: Mock) -> None:
        """Test set_secret with BotoCoreError."""
        # Arrange
        from botocore.exceptions import NoCredentialsError

        mock_client = MagicMock()
        mock_client.put_secret_value.side_effect = NoCredentialsError()
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Act & Assert
        with pytest.raises(SecretWriteError) as exc_info:
            secret_mgr.set_secret("test/secret", "value")

        assert "AWS SDK error writing secret" in str(exc_info.value)

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_set_secret_value_error(self, mock_boto_client: Mock) -> None:
        """Test set_secret with ValueError (e.g., invalid secret format)."""
        # Arrange
        mock_client = MagicMock()
        mock_client.put_secret_value.side_effect = ValueError("Secret value exceeds size limit")
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Act & Assert
        with pytest.raises(SecretWriteError) as exc_info:
            secret_mgr.set_secret("test/secret", "x" * 100000)

        assert "Invalid secret value or configuration" in str(exc_info.value)


class TestAWSSecretsManagerSetSecretRetry:
    """Test suite for set_secret retry logic."""

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_set_secret_retries_on_transient_error(self, mock_boto_client: Mock) -> None:
        """Test set_secret retries on transient errors (e.g., ThrottlingException)."""
        # Arrange
        mock_client = MagicMock()
        throttling_error = ClientError({"Error": {"Code": "ThrottlingException"}}, "PutSecretValue")
        mock_client.put_secret_value.side_effect = [
            throttling_error,
            {"VersionId": "v1"},
        ]
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Act
        secret_mgr.set_secret("test/secret", "value")

        # Assert
        # Should be called twice: once for the failure, once for the success
        assert mock_client.put_secret_value.call_count == 2

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_set_secret_does_not_retry_on_permanent_error(self, mock_boto_client: Mock) -> None:
        """Test set_secret does NOT retry on permanent errors (e.g., AccessDeniedException)."""
        # Arrange
        mock_client = MagicMock()
        access_denied_error = ClientError(
            {"Error": {"Code": "AccessDeniedException"}}, "PutSecretValue"
        )
        mock_client.put_secret_value.side_effect = access_denied_error
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Act & Assert
        with pytest.raises(SecretWriteError):
            secret_mgr.set_secret("test/secret", "value")

        # Should be called only once, as permanent errors are not retried
        assert mock_client.put_secret_value.call_count == 1

    @pytest.mark.unit()
    @patch("libs.platform.secrets.aws_backend.boto3.client")
    def test_set_secret_exhausts_retries_raises_original_exception(
        self, mock_boto_client: Mock
    ) -> None:
        """Test set_secret raises original exception (not RetryError) when retries exhausted."""
        # Arrange
        mock_client = MagicMock()
        throttling_error = ClientError(
            {"Error": {"Code": "TooManyRequestsException"}}, "PutSecretValue"
        )
        # Always fail with transient error to exhaust retries
        mock_client.put_secret_value.side_effect = throttling_error
        mock_boto_client.return_value = mock_client

        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Act & Assert
        # Should raise SecretWriteError (NOT RetryError)
        with pytest.raises(SecretWriteError) as exc_info:
            secret_mgr.set_secret("test/secret", "value")

        # Verify it's the expected domain exception with context
        assert "AWS error writing secret" in str(exc_info.value)
        assert "TooManyRequestsException" in str(exc_info.value)

        # Should be called 3 times (initial + 2 retries)
        assert mock_client.put_secret_value.call_count == 3


class TestAWSSecretsManagerTransientErrorDetection:
    """Test suite for _is_transient_aws_error function."""

    @pytest.mark.unit()
    def test_is_transient_throttling_exception(self) -> None:
        """Test ThrottlingException is identified as transient."""
        # Arrange
        from libs.platform.secrets.aws_backend import _is_transient_aws_error

        error = ClientError({"Error": {"Code": "ThrottlingException"}}, "Operation")

        # Act
        result = _is_transient_aws_error(error)

        # Assert
        assert result is True

    @pytest.mark.unit()
    def test_is_transient_service_unavailable(self) -> None:
        """Test ServiceUnavailable is identified as transient."""
        # Arrange
        from libs.platform.secrets.aws_backend import _is_transient_aws_error

        error = ClientError({"Error": {"Code": "ServiceUnavailable"}}, "Operation")

        # Act
        result = _is_transient_aws_error(error)

        # Assert
        assert result is True

    @pytest.mark.unit()
    def test_is_transient_internal_service_error(self) -> None:
        """Test InternalServiceError is identified as transient."""
        # Arrange
        from libs.platform.secrets.aws_backend import _is_transient_aws_error

        error = ClientError({"Error": {"Code": "InternalServiceError"}}, "Operation")

        # Act
        result = _is_transient_aws_error(error)

        # Assert
        assert result is True

    @pytest.mark.unit()
    def test_is_not_transient_access_denied(self) -> None:
        """Test AccessDeniedException is identified as permanent."""
        # Arrange
        from libs.platform.secrets.aws_backend import _is_transient_aws_error

        error = ClientError({"Error": {"Code": "AccessDeniedException"}}, "Operation")

        # Act
        result = _is_transient_aws_error(error)

        # Assert
        assert result is False

    @pytest.mark.unit()
    def test_is_not_transient_resource_not_found(self) -> None:
        """Test ResourceNotFoundException is identified as permanent."""
        # Arrange
        from libs.platform.secrets.aws_backend import _is_transient_aws_error

        error = ClientError({"Error": {"Code": "ResourceNotFoundException"}}, "Operation")

        # Act
        result = _is_transient_aws_error(error)

        # Assert
        assert result is False

    @pytest.mark.unit()
    def test_is_transient_botocore_error(self) -> None:
        """Test BotoCoreError is identified as transient."""
        # Arrange
        from botocore.exceptions import EndpointConnectionError

        from libs.platform.secrets.aws_backend import _is_transient_aws_error

        error = EndpointConnectionError(endpoint_url="https://test.com")

        # Act
        result = _is_transient_aws_error(error)

        # Assert
        assert result is True

    @pytest.mark.unit()
    def test_is_not_transient_other_exception(self) -> None:
        """Test non-AWS exceptions are identified as permanent."""
        # Arrange
        from libs.platform.secrets.aws_backend import _is_transient_aws_error

        error = ValueError("Some error")

        # Act
        result = _is_transient_aws_error(error)

        # Assert
        assert result is False
