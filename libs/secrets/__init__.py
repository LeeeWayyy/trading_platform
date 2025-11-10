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
    >>> from libs.secrets import create_secret_manager
    >>> secret_mgr = create_secret_manager()  # Reads SECRET_BACKEND env var
    >>> db_password = secret_mgr.get_secret("database/password")
    >>> alpaca_key = secret_mgr.get_secret("alpaca/api_key_id")

Backend Selection (via SECRET_BACKEND environment variable):
    - "vault" → VaultSecretManager (production: HashiCorp Vault)
    - "aws" → AWSSecretsManager (production: AWS Secrets Manager)
    - "env" → EnvSecretManager (local development: .env files)

Security Requirements:
    - AC12: Secret values NEVER logged (only names/paths)
    - AC22: 1-hour TTL cache (prevents trading halt during backend downtime)
    - Production guardrail: EnvSecretManager MUST NOT be used in staging/production

See Also:
    - docs/ADRs/0017-secrets-management.md - Architecture decisions
    - docs/RUNBOOKS/secrets-migration.md - Migration guide (.env → Vault/AWS)
    - docs/RUNBOOKS/secret-rotation.md - 90-day rotation procedures
"""

from libs.secrets.aws_backend import AWSSecretsManager
from libs.secrets.cache import SecretCache
from libs.secrets.env_backend import EnvSecretManager
from libs.secrets.exceptions import (
    SecretAccessError,
    SecretManagerError,
    SecretNotFoundError,
    SecretWriteError,
)
from libs.secrets.factory import create_secret_manager
from libs.secrets.manager import SecretManager
from libs.secrets.vault_backend import VaultSecretManager

# Package exports (PEP 8: __all__ defines public API)
__all__ = [
    # Core interface
    "SecretManager",
    # Factory (recommended for most use cases)
    "create_secret_manager",
    # Backend implementations (local development)
    "EnvSecretManager",
    # Backend implementations (production)
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
