"""
Secrets Management Library for Trading Platform.

This package provides a pluggable secrets management system with support for
multiple backends (Vault, AWS Secrets Manager, .env fallback).

Architecture (Abstract Factory Pattern):
    - SecretManager: Abstract interface (manager.py)
    - Backend implementations: VaultSecretManager, AWSSecretsManager, EnvSecretManager
    - Factory: create_secret_manager() selects backend via SECRET_BACKEND env var
    - Cache: 1-hour TTL in-memory cache (AC22: trading safety)

Quick Start:
    >>> from libs.platform.secrets import create_secret_manager
    >>> secret_mgr = create_secret_manager()  # Reads SECRET_BACKEND env var
    >>> db_password = secret_mgr.get_secret("database/password")
    >>> alpaca_key = secret_mgr.get_secret("alpaca/api_key_id")

Backend Selection (via SECRET_BACKEND environment variable):
    - "vault" → VaultSecretManager (production: HashiCorp Vault)
    - "aws" → AWSSecretsManager (production: AWS Secrets Manager)
    - "env" → EnvSecretManager (local/test: .env files)

Security Requirements:
    - AC12: Secret values NEVER logged (only names/paths)
    - AC22: 1-hour TTL cache (prevents trading halt during backend downtime)
    - Production guardrail: EnvSecretManager MUST NOT be used in staging/production

See Also:
    - docs/ADRs/0017-secrets-management.md - Architecture decisions
    - docs/RUNBOOKS/secrets-migration.md - Migration guide (.env → Vault/AWS)
    - docs/RUNBOOKS/secret-rotation.md - 90-day rotation procedures
"""

from typing import TYPE_CHECKING, Any

# Lazy imports: Backend implementations are only imported when explicitly accessed.
# This prevents optional dependencies (boto3 for AWS, hvac for Vault) from being
# required when using the env backend which only needs standard library.
# Use create_secret_manager() factory which handles lazy loading automatically.
if TYPE_CHECKING:
    from libs.platform.secrets.aws_backend import AWSSecretsManager as AWSSecretsManager
    from libs.platform.secrets.env_backend import EnvSecretManager as EnvSecretManager
    from libs.platform.secrets.vault_backend import VaultSecretManager as VaultSecretManager

# These modules don't have heavy dependencies, safe to import at module level
from libs.platform.secrets.cache import SecretCache
from libs.platform.secrets.exceptions import (
    SecretAccessError,
    SecretManagerError,
    SecretNotFoundError,
    SecretWriteError,
)
from libs.platform.secrets.factory import create_secret_manager
from libs.platform.secrets.manager import SecretManager


def __getattr__(name: str) -> Any:
    """Lazy load backend implementations to avoid importing optional dependencies.

    This allows `from libs.platform.secrets import AWSSecretsManager` to work without
    requiring boto3 to be installed unless AWSSecretsManager is actually used.
    """
    if name == "AWSSecretsManager":
        from libs.platform.secrets.aws_backend import AWSSecretsManager

        return AWSSecretsManager
    if name == "VaultSecretManager":
        from libs.platform.secrets.vault_backend import VaultSecretManager

        return VaultSecretManager
    if name == "EnvSecretManager":
        from libs.platform.secrets.env_backend import EnvSecretManager

        return EnvSecretManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Package exports (PEP 8: __all__ defines public API)
__all__ = [
    # Core interface
    "SecretManager",
    # Factory (recommended for most use cases)
    "create_secret_manager",
    # Backend implementations (lazy loaded)
    "EnvSecretManager",
    "VaultSecretManager",
    "AWSSecretsManager",
    # Cache utility (for custom implementations)
    "SecretCache",
    # Exceptions (callers should catch these)
    "SecretManagerError",
    "SecretNotFoundError",
    "SecretAccessError",
    "SecretWriteError",
]
