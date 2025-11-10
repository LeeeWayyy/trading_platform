"""
Abstract SecretManager Interface for Pluggable Secrets Backends.

This module defines the contract for all secrets management backends,
enabling swapping between Vault, AWS Secrets Manager, and .env fallback
without changing service code (Dependency Inversion Principle).

Architecture:
    SecretManager (ABC)
    ├── VaultSecretManager - HashiCorp Vault integration (vault_backend.py)
    ├── AWSSecretsManager - AWS Secrets Manager integration (aws_backend.py)
    └── EnvSecretManager - Local .env fallback (env_backend.py)

Backend selection via factory (factory.py):
    - SECRET_BACKEND=vault → VaultSecretManager
    - SECRET_BACKEND=aws → AWSSecretsManager
    - SECRET_BACKEND=env → EnvSecretManager (local dev only)

Usage Example:
    >>> from libs.secrets.factory import create_secret_manager
    >>> secret_mgr = create_secret_manager()  # Reads SECRET_BACKEND env var
    >>> db_password = secret_mgr.get_secret("database/password")
    >>> alpaca_key = secret_mgr.get_secret("alpaca/api_key_id")

Security Requirements:
    - AC12: Secret values NEVER logged (only names/paths in exceptions)
    - AC22: Backends SHOULD implement 1-hour TTL caching (trading safety)
    - Backends MUST NOT persist secrets to disk (in-memory only)

See Also:
    - docs/ADRs/0017-secrets-management.md - Architecture decisions
    - docs/RUNBOOKS/secrets-migration.md - Migration guide
    - docs/RUNBOOKS/secret-rotation.md - Rotation procedures
"""

from abc import ABC, abstractmethod
from types import TracebackType

from libs.secrets.exceptions import (
    SecretAccessError,  # noqa: F401 - Used in docstrings for documentation
    SecretNotFoundError,  # noqa: F401 - Used in docstrings for documentation
    SecretWriteError,  # noqa: F401 - Used in docstrings for documentation
)


class SecretManager(ABC):
    """
    Abstract base class for all secrets management backends.

    This interface defines the contract that all backend implementations
    must follow, enabling backend swapping without service code changes
    (Abstract Factory Pattern + Dependency Injection).

    Implementations:
        - VaultSecretManager: HashiCorp Vault via hvac library
        - AWSSecretsManager: AWS Secrets Manager via boto3
        - EnvSecretManager: Local .env fallback (local development only)

    Thread Safety:
        - Implementations MUST be thread-safe (multiple concurrent get_secret calls)
        - Recommended: Use connection pooling for HTTP backends (Vault, AWS)

    Caching:
        - Implementations SHOULD cache secrets with 1-hour TTL (AC22: trading safety)
        - Cache MUST be in-memory only (NEVER persist to disk)
        - Cache SHOULD invalidate on service restart or 401 errors

    Security:
        - NEVER log secret values (AC12: secret redaction)
        - ONLY log secret names/paths in exceptions (safe for audit logs)
        - Validate secret names to prevent path traversal (e.g., "../../../etc/passwd")

    Example:
        >>> class MySecretManager(SecretManager):
        ...     def get_secret(self, name: str) -> str:
        ...         # Implementation here
        ...         pass
        ...
        >>> secret_mgr = MySecretManager()
        >>> db_password = secret_mgr.get_secret("database/password")
    """

    @abstractmethod
    def get_secret(self, name: str) -> str:
        """
        Retrieve a secret value from the backend.

        This method fetches a secret by name/path. Implementations SHOULD:
        1. Check in-memory cache first (1-hour TTL)
        2. If cache miss, fetch from backend (Vault/AWS/env)
        3. Cache the value before returning
        4. Log access (timestamp, service, secret name) but NEVER the value

        Args:
            name: Secret name in hierarchical path format (e.g., "database/password", "alpaca/api_key_id")
                 This convention is used by ALL backends, ensuring client code
                 doesn't need to know which backend is in use. Each backend is
                 responsible for mapping the hierarchical name to its internal format:
                 - Vault: "database/password" → reads secret at path "database/password" within mount point
                 - AWS: "database/password" → "database/password" (secret ID in AWS)
                 - Env: "database/password" → "DATABASE_PASSWORD" (environment variable)

        Returns:
            Secret value as string (e.g., "sk-abc123xyz" for Alpaca secret key)

        Raises:
            SecretNotFoundError: Secret doesn't exist in backend
                - Verify secret exists: `vault kv get secret/<name>`
                - Check namespace (staging vs prod)
                - Review secret naming conventions

            SecretAccessError: Authentication/authorization failure
                - Invalid credentials (expired token, wrong IAM role)
                - Network timeout (backend unreachable)
                - Backend sealed/unavailable (Vault sealed mode)

        Security:
            - NEVER log the returned secret value (AC12: secret redaction)
            - Log secret name for audit trail (timestamp, service, name)

        Performance:
            - First call: ~50-100ms (HTTP fetch from backend)
            - Cached calls: <1ms (in-memory lookup)
            - Cache TTL: 1 hour (AC22: trading safety during backend downtime)

        Example:
            >>> db_password = secret_mgr.get_secret("database/password")
            >>> print(db_password)  # WRONG: Exposes secret in logs
            >>> logger.info(f"Loaded secret: database/password")  # CORRECT: Name only
        """
        pass

    @abstractmethod
    def list_secrets(self, prefix: str | None = None) -> list[str]:
        """
        List all available secret names (optional operation).

        This method returns a list of secret names/paths, useful for:
        - Verification after migration (all expected secrets present)
        - Debugging (check which secrets are available)
        - Audit reporting (inventory of secrets)

        Args:
            prefix: Optional filter prefix (e.g., "database/" returns only DB secrets)
                   If None, returns all secrets in the namespace

        Returns:
            List of secret names/paths (e.g., ["database/password", "alpaca/api_key_id"])
            Returns ONLY names, NEVER values (AC12: secret redaction)

        Raises:
            SecretAccessError: Permission denied or backend unreachable
            NotImplementedError: Backend doesn't support listing (e.g., some AWS configurations)

        Security:
            - Returns secret NAMES only, NEVER values
            - Check list permissions (some backends restrict listing)

        Example:
            >>> all_secrets = secret_mgr.list_secrets()
            >>> print(all_secrets)
            ['database/password', 'alpaca/api_key_id', 'alpaca/api_secret_key']

            >>> db_secrets = secret_mgr.list_secrets(prefix="database/")
            >>> print(db_secrets)
            ['database/password']
        """
        pass

    @abstractmethod
    def set_secret(self, name: str, value: str) -> None:
        """
        Write or update a secret in the backend.

        This method creates a new secret or updates an existing one.
        Used for:
        - Initial secret population (migration from .env)
        - Secret rotation (zero-downtime rotation workflow)
        - Programmatic secret generation (future: dynamic DB credentials)

        Args:
            name: Secret name/path (same format as get_secret)
            value: Secret value to store (e.g., new password after rotation)

        Returns:
            None (success indicated by no exception)

        Raises:
            SecretWriteError: Write operation failed
                - Insufficient permissions (read-only access)
                - Backend in read-only mode (Vault standby node)
                - Network failure during write

            SecretAccessError: Authentication failure
                - Invalid credentials (expired token, wrong IAM role)

        Security:
            - NEVER log the secret value (AC12: secret redaction)
            - Log secret name + operation for audit trail
            - Invalidate cache after write (prevent stale cached values)

        Side Effects:
            - Invalidates in-memory cache for this secret (forces fresh fetch)
            - Logs audit entry (timestamp, service, secret name, operation=write)
            - May create secret version history (Vault/AWS versioning)

        Example:
            >>> # Zero-downtime rotation workflow
            >>> new_password = generate_secure_password()
            >>> secret_mgr.set_secret("database/password", new_password)
            >>> # Cache invalidated, next get_secret() fetches new value
        """
        pass

    def close(self) -> None:  # noqa: B027 - Intentionally optional hook with default no-op
        """
        Close connections and clean up resources (optional hook).

        This method is called when the SecretManager is no longer needed,
        typically during service shutdown. Implementations MAY:
        - Close HTTP connection pools (Vault, AWS clients)
        - Clear in-memory cache (free memory)
        - Flush audit logs (if buffered)

        Default implementation is a no-op (safe for backends without cleanup).

        Example:
            >>> secret_mgr = create_secret_manager()
            >>> try:
            ...     db_password = secret_mgr.get_secret("database/password")
            ... finally:
            ...     secret_mgr.close()  # Clean up connections
        """
        pass

    def __enter__(self) -> "SecretManager":
        """
        Context manager entry (enables `with` statement).

        Example:
            >>> with create_secret_manager() as secret_mgr:
            ...     db_password = secret_mgr.get_secret("database/password")
            ...     # secret_mgr.close() called automatically on exit
        """
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """
        Context manager exit (calls close() automatically).

        Args:
            exc_type: Exception type (if exception occurred)
            exc_val: Exception value
            exc_tb: Exception traceback

        Returns:
            None (exceptions propagate normally)
        """
        self.close()
