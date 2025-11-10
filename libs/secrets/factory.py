"""
Factory for creating SecretManager instances based on environment configuration.

This module implements the Abstract Factory Pattern for selecting secrets backends:
    - SECRET_BACKEND="vault" → VaultSecretManager (production)
    - SECRET_BACKEND="aws" → AWSSecretsManager (production)
    - SECRET_BACKEND="env" → EnvSecretManager (local development only)

Production Guardrails:
    - EnvSecretManager is ONLY allowed when DEPLOYMENT_ENV="local" (default)
    - Staging/production MUST use Vault or AWS Secrets Manager unless an explicit override flag is set
    - Factory raises SecretManagerError if configuration is invalid

Example Usage:
    >>> import os
    >>> os.environ["SECRET_BACKEND"] = "vault"
    >>> secret_mgr = create_secret_manager()
    >>> isinstance(secret_mgr, VaultSecretManager)
    True

    >>> os.environ["SECRET_BACKEND"] = "env"
    >>> os.environ["DEPLOYMENT_ENV"] = "production"
    >>> os.environ["SECRET_ALLOW_ENV_IN_NON_LOCAL"] = "1"
    >>> secret_mgr = create_secret_manager()
    EnvSecretManager(...)  # override enabled for emergency rollback

Environment Variables:
    SECRET_BACKEND (str):
        Backend selection: "vault", "aws", or "env" (default: "env")
    DEPLOYMENT_ENV (str):
        Environment name: "local", "staging", or "production" (default: "local")
    SECRET_DOTENV_PATH (str, optional):
        Absolute or relative path to a .env file for EnvSecretManager
    SECRET_ALLOW_ENV_IN_NON_LOCAL (bool, optional):
        Set to "true"/"1" to allow EnvSecretManager outside local environments (emergency use only)
    AWS_REGION (str, optional):
        AWS region for AWSSecretsManager (e.g., "us-west-2", "eu-west-1")
        Falls back to AWS_DEFAULT_REGION, then "us-east-1" if not set
    VAULT_ADDR (str, required for Vault backend):
        Vault server URL (e.g., "https://vault.example.com:8200")

See Also:
    - docs/ADRs/0017-secrets-management.md - Architecture decisions
    - libs/secrets/manager.py - SecretManager interface
"""

import logging
import os
from pathlib import Path
from typing import Final

from libs.secrets.aws_backend import AWSSecretsManager
from libs.secrets.env_backend import EnvSecretManager
from libs.secrets.exceptions import SecretManagerError
from libs.secrets.manager import SecretManager
from libs.secrets.vault_backend import VaultSecretManager

logger = logging.getLogger(__name__)

_TRUTHY_VALUES: Final[set[str]] = {"1", "true", "yes", "on"}


def _is_truthy(value: str | None) -> bool:
    """Return True when the provided environment variable looks truthy."""
    return bool(value and value.strip().lower() in _TRUTHY_VALUES)


def _resolve_dotenv_path() -> Path | None:
    """
    Resolve the .env file path to load for EnvSecretManager.

    Priority:
        1. SECRET_DOTENV_PATH (must exist, otherwise raise)
        2. "./.env" (if present)
        3. No .env file (EnvSecretManager will use current environment)
    """
    override_path = os.getenv("SECRET_DOTENV_PATH")
    if override_path:
        candidate = Path(override_path).expanduser().resolve()
        if not candidate.is_file():
            raise SecretManagerError(
                f"SECRET_DOTENV_PATH is set to '{candidate}', but the file does not exist."
            )
        return candidate

    default_path = Path.cwd() / ".env"
    return default_path if default_path.is_file() else None


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

    # Production guardrail: Prevent EnvSecretManager in staging/production unless override set
    allow_env_override = _is_truthy(os.getenv("SECRET_ALLOW_ENV_IN_NON_LOCAL"))
    if selected_backend == "env" and selected_env != "local" and not allow_env_override:
        raise SecretManagerError(
            f"EnvSecretManager not allowed in {selected_env} environment. "
            f"SECURITY VIOLATION: Plain-text .env files are LOCAL DEVELOPMENT ONLY. "
            f"Use SECRET_BACKEND='vault' or 'aws' for staging/production. "
            f"See docs/ADRs/0017-secrets-management.md for migration guide."
        )
    if selected_backend == "env" and selected_env != "local" and allow_env_override:
        logger.warning(
            "EnvSecretManager override enabled for %s environment. "
            "Use only for rollback/emergency scenarios.",
            selected_env,
        )

    # Backend selection with helpful error messages
    if selected_backend == "env":
        dotenv_path = _resolve_dotenv_path()
        if dotenv_path:
            logger.info(
                "Initializing EnvSecretManager with dotenv file",
                extra={"dotenv_path": str(dotenv_path)},
            )
            return EnvSecretManager(dotenv_path=dotenv_path)
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
        # Read AWS region from environment variables
        # Priority: AWS_REGION > AWS_DEFAULT_REGION > us-east-1 (default)
        region_name = os.environ.get("AWS_REGION") or os.environ.get(
            "AWS_DEFAULT_REGION", "us-east-1"
        )

        # AWSSecretsManager uses IAM role or AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY from env
        return AWSSecretsManager(region_name=region_name)

    else:
        # Invalid backend name - provide helpful error with available options
        raise SecretManagerError(
            f"Invalid SECRET_BACKEND: '{selected_backend}'. "
            f"Valid options: 'vault', 'aws', 'env'. "
            f"Check your environment configuration or see docs/ADRs/0017-secrets-management.md"
        )
