"""
AWS Secrets Manager Backend for Production Secrets.

This module implements AWSSecretsManager, a production-ready secrets backend
that integrates with AWS Secrets Manager via boto3. This backend is suitable
for production and staging environments with AWS infrastructure.

Architecture:
    - Uses boto3 client for AWS Secrets Manager API
    - HTTP connection pooling with automatic retries
    - IAM role-based authentication (recommended) or access key authentication
    - In-memory caching with 1-hour TTL (AC22: trading safety)
    - Thread-safe operations via threading.Lock
    - Path convention: namespace/category/key (e.g., "prod/alpaca/api_key_id")
    - Automatic retries (3 attempts, exponential backoff) for transient failures

Security Considerations:
    - AC12: Secret values NEVER logged (only names/paths)
    - IAM permissions required: secretsmanager:GetSecretValue, secretsmanager:ListSecrets
    - Optional: secretsmanager:PutSecretValue for set_secret()
    - Use IAM roles (preferred) over access keys for authentication
    - Secrets encrypted at rest with AWS KMS (automatic)
    - Audit trail via AWS CloudTrail (automatic)

Usage Example:
    >>> from libs.secrets.aws_backend import AWSSecretsManager
    >>> # IAM role authentication (recommended for EC2/ECS)
    >>> secret_mgr = AWSSecretsManager(region_name="us-east-1")
    >>> db_password = secret_mgr.get_secret("prod/database/password")
    >>>
    >>> # Access key authentication (local testing only)
    >>> secret_mgr = AWSSecretsManager(
    ...     region_name="us-east-1",
    ...     aws_access_key_id="AKIA...",
    ...     aws_secret_access_key="..."
    ... )

Migration Path:
    1. Create secrets in AWS Secrets Manager (aws secretsmanager create-secret)
    2. Grant IAM permissions to service role
    3. Configure SECRET_BACKEND=aws and AWS_REGION environment variables
    4. Test with staging environment first
    5. Rollout to production with monitoring

See Also:
    - docs/ADRs/0017-secrets-management.md - Architecture decisions
    - docs/RUNBOOKS/secrets-migration.md - Migration from .env to AWS
    - docs/RUNBOOKS/secret-rotation.md - 90-day rotation procedures
    - https://docs.aws.amazon.com/secretsmanager/ - AWS Secrets Manager docs
"""

import logging
import threading
from datetime import timedelta
from typing import cast

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from libs.secrets.cache import SecretCache
from libs.secrets.exceptions import (
    SecretAccessError,
    SecretNotFoundError,
    SecretWriteError,
)
from libs.secrets.manager import SecretManager

logger = logging.getLogger(__name__)


def _is_transient_aws_error(exception: BaseException) -> bool:
    """
    Check if an AWS exception is transient and should be retried.

    Transient errors (should retry):
        - Throttling errors
        - ServiceUnavailable
        - InternalServiceError
        - Network errors (BotoCoreError)

    Permanent errors (should NOT retry):
        - AccessDeniedException (permission denied)
        - InvalidRequestException (bad request)
        - ResourceNotFoundException (secret doesn't exist)
        - InvalidParameterException (invalid input)
        - etc.

    Args:
        exception: Exception to check

    Returns:
        True if exception is transient and should be retried, False otherwise
    """
    # Always retry network/SDK errors (BotoCoreError)
    if isinstance(exception, BotoCoreError):
        return True

    # Check if it's a ClientError with a transient error code
    if isinstance(exception, ClientError):
        error_code = exception.response.get("Error", {}).get("Code", "")

        # Transient AWS error codes that should be retried
        transient_codes = {
            "ThrottlingException",
            "TooManyRequestsException",
            "ServiceUnavailable",
            "InternalServiceError",
            "InternalFailure",
        }

        return error_code in transient_codes

    # Don't retry other exception types
    return False


class AWSSecretsManager(SecretManager):
    """
    AWS Secrets Manager backend for production secrets.

    This implementation integrates with AWS Secrets Manager via boto3, providing
    enterprise-grade secrets management for production and staging environments.
    It provides in-memory caching with 1-hour TTL for performance and resilience.

    Features:
        - IAM role-based authentication (recommended) or access key authentication
        - In-memory caching with 1-hour TTL (AC22: trading safety)
        - Thread-safe operations (threading.Lock)
        - Automatic retries with exponential backoff (transient failures)
        - KMS encryption at rest (automatic)
        - CloudTrail audit logging (automatic)

    Thread Safety:
        All operations are protected by threading.Lock for concurrent access.

    Caching:
        - Secrets cached in-memory for 1 hour (AC22: trading safety)
        - Cache invalidated on set_secret() or service restart
        - No disk persistence (security requirement)

    Security:
        - AC12: Secret values NEVER logged (only names/paths)
        - Use IAM roles (preferred) over access keys
        - Secrets encrypted at rest with KMS
        - Audit trail via CloudTrail

    IAM Permissions Required:
        - secretsmanager:GetSecretValue (mandatory for read)
        - secretsmanager:ListSecrets (mandatory for list_secrets)
        - secretsmanager:PutSecretValue (optional for set_secret)
        - secretsmanager:CreateSecret (optional for set_secret with new secrets)

    Example:
        >>> # IAM role authentication (recommended)
        >>> secret_mgr = AWSSecretsManager(region_name="us-east-1")
        >>> db_password = secret_mgr.get_secret("prod/database/password")
        >>>
        >>> # List all secrets in namespace
        >>> prod_secrets = secret_mgr.list_secrets(prefix="prod/")
        >>> print(prod_secrets)
        ['prod/database/password', 'prod/alpaca/api_key_id']
        >>>
        >>> # Update secret (requires PutSecretValue permission)
        >>> secret_mgr.set_secret("prod/database/password", "new_password")
    """

    def __init__(
        self,
        region_name: str = "us-east-1",
        aws_access_key_id: str | None = None,
        aws_secret_access_key: str | None = None,
        cache_ttl_seconds: int = 3600,
    ) -> None:
        """
        Initialize AWSSecretsManager with AWS credentials and region.

        Args:
            region_name: AWS region (e.g., "us-east-1", "us-west-2")
                        Default: "us-east-1"
            aws_access_key_id: AWS access key ID (optional, for local testing)
                              If None, uses IAM role or AWS_ACCESS_KEY_ID env var
            aws_secret_access_key: AWS secret access key (optional, for local testing)
                                  If None, uses IAM role or AWS_SECRET_ACCESS_KEY env var
            cache_ttl_seconds: Cache TTL in seconds. Default: 3600 (1 hour)
                              Set to 0 to disable caching.

        Raises:
            SecretAccessError: AWS authentication failed or region invalid
                - Verify IAM role/credentials are valid
                - Check region name is correct
                - Ensure AWS SDK can reach AWS endpoints

        Example:
            >>> # IAM role authentication (recommended for production)
            >>> secret_mgr = AWSSecretsManager(region_name="us-east-1")
            >>>
            >>> # Access key authentication (local testing only)
            >>> secret_mgr = AWSSecretsManager(
            ...     region_name="us-east-1",
            ...     aws_access_key_id="AKIA...",
            ...     aws_secret_access_key="..."
            ... )
            >>>
            >>> # Custom cache TTL (10 minutes)
            >>> secret_mgr = AWSSecretsManager(
            ...     region_name="us-east-1",
            ...     cache_ttl_seconds=600
            ... )
        """
        self._lock = threading.Lock()
        self._cache = SecretCache(ttl=timedelta(seconds=cache_ttl_seconds))
        self._region_name = region_name

        # Initialize AWS Secrets Manager client
        try:
            # Create boto3 client with provided credentials or IAM role
            client_kwargs: dict[str, str] = {"region_name": region_name}
            if aws_access_key_id is not None and aws_secret_access_key is not None:
                client_kwargs["aws_access_key_id"] = aws_access_key_id
                client_kwargs["aws_secret_access_key"] = aws_secret_access_key
                logger.info(
                    "Initializing AWS Secrets Manager with access key authentication",
                    extra={"region": region_name, "backend": "aws"},
                )
            else:
                logger.info(
                    "Initializing AWS Secrets Manager with IAM role authentication",
                    extra={"region": region_name, "backend": "aws"},
                )

            self._client = boto3.client("secretsmanager", **client_kwargs)

            # Connection validated on first get_secret() call (lazy validation)
            # Note: Intentionally NOT calling list_secrets() here to support
            # read-only IAM roles that only have GetSecretValue permission
            logger.info(
                "AWS Secrets Manager initialized successfully",
                extra={"region": region_name, "backend": "aws"},
            )

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            raise SecretAccessError(
                secret_name="aws_initialization",
                backend="aws",
                reason=(
                    f"AWS authentication failed: {error_code}. "
                    f"Verify IAM role/credentials and region ({region_name}) are correct."
                ),
            ) from e
        except BotoCoreError as e:
            raise SecretAccessError(
                secret_name="aws_initialization",
                backend="aws",
                reason=f"AWS SDK error during initialization: {e}",
            ) from e
        except Exception as e:
            raise SecretAccessError(
                secret_name="aws_initialization",
                backend="aws",
                reason=f"Unexpected error during AWS Secrets Manager initialization: {e}",
            ) from e

    def get_secret(self, name: str) -> str:
        """
        Retrieve a secret from AWS Secrets Manager.

        This method checks the in-memory cache first, then falls back to
        AWS Secrets Manager API if cache miss or expired. Secret values are
        cached for 1 hour (configurable via cache_ttl_seconds).

        Retries: 3 attempts with exponential backoff (1-5 seconds) for TRANSIENT
        AWS API failures (throttling, network errors). Permanent errors like
        ResourceNotFoundException are NOT retried.

        Args:
            name: Secret name/path (e.g., "prod/database/password", "prod/alpaca/api_key_id")
                 AWS Secrets Manager uses flat namespace, so path-like names
                 are purely organizational convention.

        Returns:
            Secret value as string

        Raises:
            SecretNotFoundError: Secret doesn't exist in AWS Secrets Manager
                - Verify secret exists: `aws secretsmanager describe-secret --secret-id <name>`
                - Check namespace (staging vs prod)
                - Review secret naming conventions

            SecretAccessError: Authentication or permission failure
                - Invalid IAM role/credentials
                - Missing secretsmanager:GetSecretValue permission
                - Network timeout (AWS API unreachable)
                - Secret marked for deletion (recovery window active)

        Security:
            - NEVER log the returned secret value (AC12)
            - Log secret name for audit trail

        Performance:
            - First call: ~50-100ms (AWS API call)
            - Cached calls: <1ms (in-memory lookup)
            - Cache TTL: 1 hour (default)

        Example:
            >>> db_password = secret_mgr.get_secret("prod/database/password")
            >>> logger.info(f"Loaded secret: prod/database/password")  # CORRECT: Name only
            >>> # print(db_password)  # WRONG: Exposes secret in logs
        """
        try:
            return self._get_secret_with_retry(name)
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")

            if error_code == "ResourceNotFoundException":
                raise SecretNotFoundError(
                    secret_name=name,
                    backend="aws",
                    additional_context=(
                        f"Secret '{name}' not found in AWS Secrets Manager "
                        f"(region: {self._region_name}). "
                        f"Verify secret exists with: aws secretsmanager describe-secret "
                        f"--secret-id {name}"
                    ),
                ) from e
            elif error_code == "InvalidRequestException":
                raise SecretAccessError(
                    secret_name=name,
                    backend="aws",
                    reason=f"Invalid request for secret '{name}': Secret marked for deletion",
                ) from e
            elif error_code == "AccessDeniedException":
                raise SecretAccessError(
                    secret_name=name,
                    backend="aws",
                    reason=(
                        f"Access denied for secret '{name}'. "
                        f"Verify IAM role has secretsmanager:GetSecretValue permission."
                    ),
                ) from e
            else:
                raise SecretAccessError(
                    secret_name=name,
                    backend="aws",
                    reason=f"AWS API error: {error_code}",
                ) from e
        except BotoCoreError as e:
            raise SecretAccessError(
                secret_name=name,
                backend="aws",
                reason=f"AWS SDK error retrieving secret: {e}",
            ) from e

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception(_is_transient_aws_error),
        reraise=True,
    )
    def _get_secret_with_retry(self, name: str) -> str:
        """
        Internal method: Retrieve secret with retry logic.

        This method contains the actual AWS API calls and allows TRANSIENT
        exceptions to propagate for retry (throttling, network errors, etc.).
        Permanent errors (ResourceNotFoundException, AccessDeniedException) are
        NOT retried and propagate immediately to the outer get_secret() method.

        Args:
            name: Secret name/path

        Returns:
            Secret value as string

        Raises:
            ClientError: AWS API error (transient errors retried, permanent errors propagate)
            BotoCoreError: AWS SDK error (always retried)
        """
        with self._lock:
            # Check cache first
            cached_value = self._cache.get(name)
            if cached_value is not None:
                logger.debug(
                    "Secret cache hit",
                    extra={"secret_name": name, "backend": "aws"},
                )
                return cached_value

            # Cache miss - fetch from AWS Secrets Manager
            response = self._client.get_secret_value(SecretId=name)

            # AWS Secrets Manager stores secrets in either SecretString or SecretBinary
            # For this application, we expect SecretString (text secrets)
            if "SecretString" in response:
                value = cast(str, response["SecretString"])
            else:
                raise SecretAccessError(
                    secret_name=name,
                    backend="aws",
                    reason="Secret is binary, expected text (SecretString)",
                )

            # Cache the value
            self._cache.set(name, value)
            logger.info(
                "Secret loaded from AWS Secrets Manager",
                extra={"secret_name": name, "backend": "aws"},
            )
            return value

    def list_secrets(self, prefix: str | None = None) -> list[str]:
        """
        List all secret names in AWS Secrets Manager (optional prefix filter).

        This method returns a list of secret names/paths, optionally filtered
        by prefix. Useful for verification and debugging.

        Retries: 3 attempts with exponential backoff (1-5 seconds) for TRANSIENT
        AWS API failures (throttling, network errors). Permanent errors like
        AccessDeniedException are NOT retried.

        **Performance Note**: This method paginates through ALL secrets in the
        region to filter by prefix. For accounts with many secrets (>1000),
        this may take several seconds. Consider caching the results if called frequently.

        Args:
            prefix: Optional filter prefix (e.g., "prod/" returns only prod secrets)
                   If None, returns ALL secrets in the region

        Returns:
            List of secret names (e.g., ["prod/database/password", "prod/alpaca/api_key_id"])
            Returns ONLY names, NEVER values (AC12: secret redaction)

        Raises:
            SecretAccessError: Permission denied or AWS API unreachable
                - Missing secretsmanager:ListSecrets permission
                - Network timeout

        Security:
            - Returns secret NAMES only, NEVER values
            - Requires secretsmanager:ListSecrets IAM permission

        Performance:
            - ~100-500ms depending on total secret count
            - Paginated API calls (100 secrets per page)
            - Consider caching results if called frequently

        Example:
            >>> # List all secrets
            >>> all_secrets = secret_mgr.list_secrets()
            >>> print(all_secrets)
            ['prod/database/password', 'staging/database/password', 'prod/alpaca/api_key_id']
            >>>
            >>> # List only production secrets
            >>> prod_secrets = secret_mgr.list_secrets(prefix="prod/")
            >>> print(prod_secrets)
            ['prod/database/password', 'prod/alpaca/api_key_id']
        """
        try:
            return self._list_secrets_with_retry(prefix)
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            if error_code == "AccessDeniedException":
                raise SecretAccessError(
                    secret_name=f"list_secrets(prefix={prefix})",
                    backend="aws",
                    reason=(
                        "Access denied for listing secrets. "
                        "Verify IAM role has secretsmanager:ListSecrets permission."
                    ),
                ) from e
            else:
                raise SecretAccessError(
                    secret_name=f"list_secrets(prefix={prefix})",
                    backend="aws",
                    reason=f"AWS API error: {error_code}",
                ) from e
        except BotoCoreError as e:
            raise SecretAccessError(
                secret_name=f"list_secrets(prefix={prefix})",
                backend="aws",
                reason=f"AWS SDK error listing secrets: {e}",
            ) from e

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception(_is_transient_aws_error),
        reraise=True,
    )
    def _list_secrets_with_retry(self, prefix: str | None = None) -> list[str]:
        """
        Internal method: List secrets with retry logic.

        This method contains the actual AWS API calls and allows TRANSIENT
        exceptions to propagate for retry (throttling, network errors, etc.).
        Permanent errors (AccessDeniedException) are NOT retried and propagate
        immediately to the outer list_secrets() method.

        Args:
            prefix: Optional filter prefix

        Returns:
            List of secret names

        Raises:
            ClientError: AWS API error (transient errors retried, permanent errors propagate)
            BotoCoreError: AWS SDK error (always retried)
        """
        with self._lock:
            secret_names: list[str] = []

            # AWS Secrets Manager uses pagination for list_secrets
            paginator = self._client.get_paginator("list_secrets")

            # Note: AWS Filters API uses exact name matching, not prefix matching
            # Therefore we use client-side filtering for all prefix queries
            for page in paginator.paginate():
                for secret in page.get("SecretList", []):
                    secret_name = secret.get("Name", "")
                    if secret_name:
                        # Client-side prefix filtering
                        if prefix is None or secret_name.startswith(prefix):
                            secret_names.append(secret_name)

            logger.info(
                "Listed secrets from AWS Secrets Manager",
                extra={
                    "count": len(secret_names),
                    "prefix": prefix,
                    "backend": "aws",
                },
            )
            return sorted(secret_names)

    def set_secret(self, name: str, value: str) -> None:
        """
        Create or update a secret in AWS Secrets Manager.

        This method creates a new secret or updates an existing one. Changes
        are versioned automatically by AWS Secrets Manager (version history available).

        Retries: 3 attempts with exponential backoff (1-5 seconds) for transient
        AWS API failures.

        Args:
            name: Secret name/path (e.g., "prod/database/password")
            value: Secret value to store (plaintext or JSON string)

        Returns:
            None (success indicated by no exception)

        Raises:
            SecretWriteError: Write operation failed
                - Insufficient IAM permissions (missing PutSecretValue/CreateSecret)
                - Secret marked for deletion (recovery window active)
                - Network failure during write

            SecretAccessError: Authentication failure
                - Invalid IAM role/credentials
                - Missing secretsmanager:PutSecretValue permission

        Security:
            - NEVER log the secret value (AC12: secret redaction)
            - Log secret name + operation for audit trail
            - Invalidate cache after write (prevent stale cached values)
            - Changes tracked via AWS CloudTrail automatically

        Side Effects:
            - Invalidates in-memory cache for this secret (forces fresh fetch)
            - Creates new secret version in AWS (version history preserved)
            - Logs audit entry (timestamp, service, secret name, operation=write)

        IAM Permissions Required:
            - secretsmanager:PutSecretValue (update existing secret)
            - secretsmanager:CreateSecret (create new secret if doesn't exist)

        Example:
            >>> # Zero-downtime rotation workflow
            >>> new_password = generate_secure_password()
            >>> secret_mgr.set_secret("prod/database/password", new_password)
            >>> # Cache invalidated, next get_secret() fetches new value
            >>>
            >>> # Create new secret
            >>> secret_mgr.set_secret("prod/new_service/api_key", "sk-abc123")
        """
        try:
            # Call internal retry method (retries transient errors)
            self._set_secret_with_retry(name, value)

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")

            if error_code == "AccessDeniedException":
                raise SecretWriteError(
                    secret_name=name,
                    backend="aws",
                    reason=(
                        "Access denied for writing secret. "
                        "Verify IAM role has secretsmanager:PutSecretValue "
                        "and secretsmanager:CreateSecret permissions."
                    ),
                ) from e
            elif error_code == "InvalidRequestException":
                raise SecretWriteError(
                    secret_name=name,
                    backend="aws",
                    reason=f"Invalid request for secret '{name}': Secret marked for deletion",
                ) from e
            else:
                raise SecretWriteError(
                    secret_name=name,
                    backend="aws",
                    reason=f"AWS error writing secret: {error_code}",
                ) from e

        except BotoCoreError as e:
            raise SecretWriteError(
                secret_name=name,
                backend="aws",
                reason=f"AWS SDK error writing secret: {e}",
            ) from e

        except Exception as e:
            raise SecretWriteError(
                secret_name=name,
                backend="aws",
                reason=f"Unexpected error writing secret: {e}",
            ) from e

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception(_is_transient_aws_error),
        reraise=True,
    )
    def _set_secret_with_retry(self, name: str, value: str) -> None:
        """
        Internal method: Create or update secret with retry logic.

        This method contains the actual AWS API calls and allows TRANSIENT
        exceptions to propagate for retry (throttling, network errors, etc.).
        Permanent errors (AccessDeniedException, InvalidRequestException) are
        NOT retried and propagate immediately to the outer set_secret() method.

        Args:
            name: Secret name/path
            value: Secret value to store

        Raises:
            ClientError: AWS API error (transient errors retried, permanent errors propagate)
            BotoCoreError: AWS SDK error (always retried)
        """
        with self._lock:
            # Try to update existing secret first (PutSecretValue)
            try:
                self._client.put_secret_value(
                    SecretId=name,
                    SecretString=value,
                )
                logger.info(
                    "Secret updated in AWS Secrets Manager",
                    extra={"secret_name": name, "backend": "aws"},
                )

            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "Unknown")

                if error_code == "ResourceNotFoundException":
                    # Secret doesn't exist, create it (CreateSecret)
                    self._client.create_secret(
                        Name=name,
                        SecretString=value,
                    )
                    logger.info(
                        "Secret created in AWS Secrets Manager",
                        extra={"secret_name": name, "backend": "aws"},
                    )
                else:
                    # Re-raise other ClientErrors for retry or error handling
                    raise

            # Invalidate cache (force fresh fetch on next get)
            self._cache.invalidate(name)

    def close(self) -> None:
        """
        Clean up resources (clear cache).

        This method clears the in-memory cache. Boto3 clients don't require
        explicit closing. Called automatically when used as context manager.

        Example:
            >>> secret_mgr = AWSSecretsManager(region_name="us-east-1")
            >>> try:
            ...     db_password = secret_mgr.get_secret("prod/database/password")
            ... finally:
            ...     secret_mgr.close()  # Clear cache
        """
        self._cache.clear()
        # Boto3 clients don't need explicit close, but we clear cache
        logger.info(
            "AWSSecretsManager closed, cache cleared",
            extra={"backend": "aws"},
        )
