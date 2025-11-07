"""
Factory for creating SecretManager instances based on environment configuration.

This module implements the Abstract Factory Pattern for selecting secrets backends:
    - SECRET_BACKEND="vault" → VaultSecretManager (production)
    - SECRET_BACKEND="aws" → AWSSecretsManager (production)
    - SECRET_BACKEND="env" → EnvSecretManager (local development only)

Production Guardrails:
    - EnvSecretManager is ONLY allowed when DEPLOYMENT_ENV="local" (default)
    - Staging/production MUST use Vault or AWS Secrets Manager
    - Factory raises SecretManagerError if configuration is invalid

Example Usage:
    >>> import os
    >>> os.environ["SECRET_BACKEND"] = "vault"
    >>> secret_mgr = create_secret_manager()
    >>> isinstance(secret_mgr, VaultSecretManager)
    True

    >>> os.environ["SECRET_BACKEND"] = "env"
    >>> os.environ["DEPLOYMENT_ENV"] = "production"
    >>> secret_mgr = create_secret_manager()
    SecretManagerError: EnvSecretManager not allowed in production environment

Environment Variables:
    SECRET_BACKEND (str):
        Backend selection: "vault", "aws", or "env" (default: "env")
    DEPLOYMENT_ENV (str):
        Environment name: "local", "staging", or "production" (default: "local")

See Also:
    - docs/ADRs/0017-secrets-management.md - Architecture decisions
    - libs/secrets/manager.py - SecretManager interface
"""

import os

from libs.secrets.aws_backend import AWSSecretsManager
from libs.secrets.env_backend import EnvSecretManager
from libs.secrets.exceptions import SecretManagerError
from libs.secrets.manager import SecretManager
from libs.secrets.vault_backend import VaultSecretManager


def create_secret_manager(
    backend: str | None = None,
    deployment_env: str | None = None,
) -> SecretManager:
    """
    Create a SecretManager instance based on environment configuration.

    Factory function that selects the appropriate secrets backend implementation
    based on SECRET_BACKEND and DEPLOYMENT_ENV environment variables.

    Production Guardrails:
        - EnvSecretManager is ONLY allowed in local development (DEPLOYMENT_ENV="local")
        - Staging and production environments MUST use Vault or AWS Secrets Manager
        - Invalid backend names raise SecretManagerError with helpful message

    Args:
        backend: Backend name override ("vault", "aws", "env").
                 If None, reads from SECRET_BACKEND env var (default: "env").
        deployment_env: Deployment environment override ("local", "staging", "production").
                        If None, reads from DEPLOYMENT_ENV env var (default: "local").

    Returns:
        SecretManager: Configured backend instance (VaultSecretManager, AWSSecretsManager,
                       or EnvSecretManager).

    Raises:
        SecretManagerError: If backend is invalid or EnvSecretManager used in non-local env.

    Examples:
        >>> # Local development (default)
        >>> secret_mgr = create_secret_manager()  # Uses EnvSecretManager
        >>> secret_mgr.get_secret("database/password")
        "local_dev_password"

        >>> # Production with Vault
        >>> os.environ["SECRET_BACKEND"] = "vault"
        >>> os.environ["DEPLOYMENT_ENV"] = "production"
        >>> secret_mgr = create_secret_manager()
        >>> isinstance(secret_mgr, VaultSecretManager)
        True

        >>> # Production with AWS Secrets Manager
        >>> secret_mgr = create_secret_manager(backend="aws", deployment_env="production")
        >>> isinstance(secret_mgr, AWSSecretsManager)
        True

        >>> # Invalid: EnvSecretManager in production (raises error)
        >>> secret_mgr = create_secret_manager(backend="env", deployment_env="production")
        SecretManagerError: EnvSecretManager not allowed in production environment...

    See Also:
        - docs/ADRs/0017-secrets-management.md - Backend selection rationale
        - docs/RUNBOOKS/secrets-migration.md - Migration guide
    """
    # Read configuration from environment variables with defaults
    selected_backend_raw = backend if backend is not None else os.getenv("SECRET_BACKEND", "env")
    selected_env_raw = (
        deployment_env if deployment_env is not None else os.getenv("DEPLOYMENT_ENV", "local")
    )

    # Normalize to lowercase for case-insensitive comparison
    selected_backend = selected_backend_raw.lower().strip()
    selected_env = selected_env_raw.lower().strip()

    # Default empty string to "env" backend
    if not selected_backend:
        selected_backend = "env"

    # Production guardrail: Prevent EnvSecretManager in staging/production
    if selected_backend == "env" and selected_env != "local":
        raise SecretManagerError(
            f"EnvSecretManager not allowed in {selected_env} environment. "
            f"SECURITY VIOLATION: Plain-text .env files are LOCAL DEVELOPMENT ONLY. "
            f"Use SECRET_BACKEND='vault' or 'aws' for staging/production. "
            f"See docs/ADRs/0017-secrets-management.md for migration guide."
        )

    # Backend selection with helpful error messages
    if selected_backend == "env":
        return EnvSecretManager()

    elif selected_backend == "vault":
        # VaultSecretManager requires VAULT_ADDR environment variable
        vault_url = os.getenv("VAULT_ADDR")
        if not vault_url:
            raise SecretManagerError(
                "VAULT_ADDR environment variable required for Vault backend. "
                "Set VAULT_ADDR to your Vault server URL (e.g., 'https://vault.company.com:8200'). "
                "See docs/ADRs/0017-secrets-management.md for configuration guidance."
            )
        return VaultSecretManager(vault_url=vault_url)

    elif selected_backend == "aws":
        # AWSSecretsManager constructor reads AWS_REGION (or defaults to us-east-1)
        # and uses IAM role or AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY from env
        return AWSSecretsManager()

    else:
        # Invalid backend name - provide helpful error with available options
        raise SecretManagerError(
            f"Invalid SECRET_BACKEND: '{selected_backend}'. "
            f"Valid options: 'vault', 'aws', 'env'. "
            f"Check your environment configuration or see docs/ADRs/0017-secrets-management.md"
        )
