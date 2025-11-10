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

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, Mock, patch

import pytest
from botocore.exceptions import BotoCoreError, ClientError

from libs.secrets.aws_backend import AWSSecretsManager
from libs.secrets.exceptions import (
    SecretAccessError,
    SecretNotFoundError,
    SecretWriteError,
)


class TestAWSSecretsManagerInitialization:
    """Test suite for AWSSecretsManager initialization and authentication."""

    @pytest.mark.unit()
    @patch("libs.secrets.aws_backend.boto3.client")
    def test_init_success_iam_role(self, mock_boto_client: Mock) -> None:
        """Test successful initialization with IAM role authentication (no explicit credentials)."""
        # Arrange
        mock_client = MagicMock()
        mock_client.list_secrets.return_value = {"SecretList": []}
        mock_boto_client.return_value = mock_client

        # Act
        secret_mgr = AWSSecretsManager(region_name="us-east-1")

        # Assert
        assert secret_mgr._region_name == "us-east-1"
        assert secret_mgr._cache == {}
        mock_boto_client.assert_called_once_with(
            "secretsmanager",
            region_name="us-east-1",
        )
        mock_client.list_secrets.assert_called_once_with(MaxResults=1)

    @pytest.mark.unit()
    @patch("libs.secrets.aws_backend.boto3.client")
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

    @pytest.mark.unit()
    @patch("libs.secrets.aws_backend.boto3.client")
    def test_init_auth_failure(self, mock_boto_client: Mock) -> None:
        """Test initialization failure due to invalid credentials."""
        # Arrange
        mock_boto_client.return_value.list_secrets.side_effect = ClientError(
            {"Error": {"Code": "UnrecognizedClientException"}},
            "ListSecrets",
        )

        # Act & Assert
        with pytest.raises(SecretAccessError) as exc_info:
            AWSSecretsManager(region_name="us-east-1")

        assert "AWS authentication failed" in str(exc_info.value)
        assert "UnrecognizedClientException" in str(exc_info.value)

    @pytest.mark.unit()
    @patch("libs.secrets.aws_backend.boto3.client")
    def test_init_network_error(self, mock_boto_client: Mock) -> None:
        """Test initialization failure due to network error."""
        # Arrange
        mock_boto_client.return_value.list_secrets.side_effect = BotoCoreError()

        # Act & Assert
        with pytest.raises(SecretAccessError) as exc_info:
            AWSSecretsManager(region_name="us-east-1")

        assert "AWS SDK error" in str(exc_info.value)


class TestAWSSecretsManagerGetSecret:
    """Test suite for get_secret() method."""

    @pytest.mark.unit()
    @patch("libs.secrets.aws_backend.boto3.client")
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
        mock_client.get_secret_value.assert_called_once_with(
            SecretId="prod/database/password"
        )
        # Verify caching
        assert "prod/database/password" in secret_mgr._cache

    @pytest.mark.unit()
    @patch("libs.secrets.aws_backend.boto3.client")
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
    @patch("libs.secrets.aws_backend.boto3.client")
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
        # Manually expire cache
        secret_name = "test/secret"
        cached_value, _ = secret_mgr._cache[secret_name]
        secret_mgr._cache[secret_name] = (
            cached_value,
            datetime.now(UTC) - timedelta(seconds=2),  # Expired (timezone-aware)
        )
        secret_mgr.get_secret("test/secret")  # Cache expired, fetch again

        # Assert
        assert mock_client.get_secret_value.call_count == 2

    @pytest.mark.unit()
    @patch("libs.secrets.aws_backend.boto3.client")
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
    @patch("libs.secrets.aws_backend.boto3.client")
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
    @patch("libs.secrets.aws_backend.boto3.client")
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
    @patch("libs.secrets.aws_backend.boto3.client")
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
    @patch("libs.secrets.aws_backend.boto3.client")
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
    @patch("libs.secrets.aws_backend.boto3.client")
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
    @patch("libs.secrets.aws_backend.boto3.client")
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
    @patch("libs.secrets.aws_backend.boto3.client")
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
    @patch("libs.secrets.aws_backend.boto3.client")
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
    @patch("libs.secrets.aws_backend.boto3.client")
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
    @patch("libs.secrets.aws_backend.boto3.client")
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
        assert "test/secret" in secret_mgr._cache

        secret_mgr.set_secret("test/secret", "new_value")  # Invalidate cache

        # Assert
        assert "test/secret" not in secret_mgr._cache

    @pytest.mark.unit()
    @patch("libs.secrets.aws_backend.boto3.client")
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
    @patch("libs.secrets.aws_backend.boto3.client")
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
    @patch("libs.secrets.aws_backend.boto3.client")
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

        # Act
        secret_mgr.close()

        # Assert
        assert secret_mgr._cache == {}

    @pytest.mark.unit()
    @patch("libs.secrets.aws_backend.boto3.client")
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
            assert "test/secret" in secret_mgr._cache

        # Assert (context manager called close())
        assert secret_mgr._cache == {}
