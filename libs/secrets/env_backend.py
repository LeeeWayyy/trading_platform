"""
Environment Variable Secret Manager Backend.

This module implements EnvSecretManager, a local development secrets backend
that reads from environment variables and .env files. This backend is intended
for local development only and MUST NOT be used in production (AC7).

Architecture:
    - Reads secrets from environment variables (os.environ)
    - Supports .env file loading via python-dotenv
    - In-memory caching with 1-hour TTL (AC22: trading safety)
    - Thread-safe operations via threading.Lock
    - UPPERCASE env var naming convention (e.g., DATABASE_PASSWORD)

Security Considerations:
    - AC12: Secret values NEVER logged (only names/paths)
    - AC7: Local development only (not production-safe)
    - .env files MUST be in .gitignore (prevent accidental commits)
    - set_secret() updates environment ONLY (does not persist to .env)

Usage Example:
    >>> from libs.secrets.env_backend import EnvSecretManager
    >>> secret_mgr = EnvSecretManager(dotenv_path=".env")
    >>> db_password = secret_mgr.get_secret("DATABASE_PASSWORD")
    >>> alpaca_key = secret_mgr.get_secret("ALPACA_API_KEY_ID")

Migration Path:
    1. Local development: Use EnvSecretManager with .env file
    2. Staging/Production: Migrate to VaultSecretManager or AWSSecretsManager
    3. See docs/RUNBOOKS/secrets-migration.md for migration procedures

See Also:
    - docs/ADRs/0017-secrets-management.md - Architecture decisions
    - docs/RUNBOOKS/secrets-migration.md - Migration guide
    - docs/GETTING_STARTED/SETUP.md - Local setup instructions
"""

import logging
import os
import threading
from datetime import timedelta
from pathlib import Path

from dotenv import load_dotenv

from libs.secrets.cache import SecretCache
from libs.secrets.exceptions import (
    SecretAccessError,
    SecretNotFoundError,
    SecretWriteError,
)
from libs.secrets.manager import SecretManager

logger = logging.getLogger(__name__)


class EnvSecretManager(SecretManager):
    """
    Environment variable secrets backend for local development.

    This implementation reads secrets from environment variables and supports
    .env file loading via python-dotenv. It provides in-memory caching with
    1-hour TTL for consistency with other backends (AC22).

    **WARNING**: This backend is for LOCAL DEVELOPMENT ONLY (AC7).
    DO NOT use in staging or production - use Vault or AWS Secrets Manager.

    Features:
        - Reads from environment variables (os.environ)
        - Loads .env file on initialization (if provided)
        - In-memory caching with 1-hour TTL (AC22)
        - Thread-safe operations (threading.Lock)
        - UPPERCASE naming convention (DATABASE_PASSWORD, not database/password)

    Thread Safety:
        All operations are protected by threading.Lock for concurrent access.

    Caching:
        - Secrets cached in-memory for 1 hour (AC22: trading safety)
        - Cache invalidated on set_secret() or service restart
        - No disk persistence (security requirement)

    Security:
        - AC12: Secret values NEVER logged (only names)
        - AC7: Local development only (not production-safe)
        - .env files MUST be in .gitignore

    Example:
        >>> # Load .env file and access secrets
        >>> secret_mgr = EnvSecretManager(dotenv_path=".env")
        >>> db_password = secret_mgr.get_secret("DATABASE_PASSWORD")
        >>>
        >>> # List all environment variables with prefix
        >>> db_secrets = secret_mgr.list_secrets(prefix="DATABASE_")
        >>> print(db_secrets)
        ['DATABASE_PASSWORD', 'DATABASE_HOST', 'DATABASE_PORT']
        >>>
        >>> # Update secret in environment (runtime only, not persisted)
        >>> secret_mgr.set_secret("DATABASE_PASSWORD", "new_password")
    """

    def __init__(
        self,
        dotenv_path: str | Path | None = None,
        cache_ttl_seconds: int = 3600,
    ) -> None:
        """
        Initialize EnvSecretManager with optional .env file loading.

        Args:
            dotenv_path: Optional path to .env file to load.
                        If None, only reads from existing environment variables.
                        Default: None (no .env file loaded)
            cache_ttl_seconds: Cache TTL in seconds. Default: 3600 (1 hour)
                              Set to 0 to disable caching.

        Raises:
            SecretAccessError: If .env file path is provided but file doesn't exist

        Example:
            >>> # Load .env file
            >>> secret_mgr = EnvSecretManager(dotenv_path=".env")
            >>>
            >>> # Use existing environment variables only
            >>> secret_mgr = EnvSecretManager()
            >>>
            >>> # Custom cache TTL (10 minutes)
            >>> secret_mgr = EnvSecretManager(dotenv_path=".env", cache_ttl_seconds=600)
        """
        self._lock = threading.Lock()
        self._cache = SecretCache(ttl=timedelta(seconds=cache_ttl_seconds))
        self._dotenv_path = dotenv_path

        # Load .env file if provided
        if dotenv_path is not None:
            dotenv_file = Path(dotenv_path)
            if not dotenv_file.exists():
                raise SecretAccessError(
                    secret_name="dotenv_file",
                    backend="env",
                    reason=f".env file not found: {dotenv_path}",
                )

            # Load .env file into environment
            load_dotenv(dotenv_path=dotenv_file, override=True)
            logger.info(
                "Loaded .env file for secrets management",
                extra={"dotenv_path": str(dotenv_file), "backend": "env"},
            )
        else:
            logger.info(
                "Using environment variables without .env file",
                extra={"backend": "env"},
            )

    def get_secret(self, name: str) -> str:
        """
        Retrieve a secret from environment variables.

        This method checks the in-memory cache first, then falls back to
        os.environ if cache miss or expired. Secret values are cached for
        1 hour (configurable via cache_ttl_seconds).

        Args:
            name: Hierarchical secret name (e.g., "database/password", "alpaca/api_key_id")
                 Converted to environment variable format (e.g., "DATABASE_PASSWORD", "ALPACA_API_KEY_ID")

        Returns:
            Secret value as string

        Raises:
            SecretNotFoundError: Environment variable not set
                - Verify variable is set: `echo $DATABASE_PASSWORD`
                - Check .env file contains the variable
                - Ensure .env file was loaded successfully

            SecretAccessError: Cache access failure (rare)

        Security:
            - NEVER log the returned secret value (AC12)
            - Log secret name for audit trail

        Performance:
            - First call: ~1ms (os.environ lookup)
            - Cached calls: <0.1ms (in-memory lookup)
            - Cache TTL: 1 hour (default)

        Example:
            >>> db_password = secret_mgr.get_secret("database/password")
            >>> logger.info(f"Loaded secret: database/password")  # CORRECT: Name only
            >>> # print(db_password)  # WRONG: Exposes secret in logs
        """
        with self._lock:
            # Check cache first
            cached_value = self._cache.get(name)
            if cached_value is not None:
                logger.debug(
                    "Secret cache hit",
                    extra={"secret_name": name, "backend": "env"},
                )
                return cached_value

            # Convert hierarchical name to environment variable format
            # "database/password" -> "DATABASE_PASSWORD"
            env_var_name = name.upper().replace("/", "_")

            # Cache miss - fetch from environment
            value = os.environ.get(env_var_name)
            if value is None:
                raise SecretNotFoundError(
                    secret_name=name,
                    backend="env",
                    additional_context=(
                        f"Environment variable '{env_var_name}' not set. "
                        f"Verify .env file contains this variable or set it manually."
                    ),
                )

            # Type narrowed: value is str here (None case raises above)
            # Cache the value
            self._cache.set(name, value)
            logger.info(
                "Secret loaded from environment",
                extra={"secret_name": name, "backend": "env"},
            )
            return value

    def list_secrets(self, prefix: str | None = None) -> list[str]:
        """
        List all environment variable names (optional prefix filter).

        This method returns a list of environment variable names, optionally
        filtered by prefix. Useful for verification and debugging.

        ⚠️ **CRITICAL SECURITY WARNING** ⚠️
        ========================================
        Calling list_secrets() WITHOUT a prefix will return ALL environment
        variables in the current process, including:

        - System variables (PATH, HOME, USER, SHELL, etc.)
        - Infrastructure secrets (AWS credentials, DB passwords, API keys)
        - Application config (URLs, ports, feature flags)
        - CI/CD variables (build metadata, deployment tokens)

        **RECOMMENDATION**: ALWAYS provide a prefix filter to scope the results
        to application-specific secrets only (e.g., "ALPACA_", "DATABASE_").

        Exposing system environment variables in logs or monitoring dashboards
        can leak sensitive infrastructure details that aid attackers in
        reconnaissance and privilege escalation attacks.

        ⚠️ **NAMING AMBIGUITY LIMITATION** ⚠️
        ======================================
        The reverse conversion from environment variables to hierarchical names
        is LOSSY and AMBIGUOUS. Underscores in the original hierarchical name
        cannot be distinguished from path separators:

        - `ALPACA_API_KEY_ID` could be `alpaca/api_key_id` OR `alpaca/api/key/id`
        - This method always converts to `alpaca/api/key/id` (all underscores → slashes)

        **IMPACT**: list_secrets() may return different names than originally used
        in set_secret(). However, get_secret() and set_secret() work correctly
        because they only convert hierarchical → env var (unambiguous direction).

        **WORKAROUND**: Avoid underscores in hierarchical secret names when using
        EnvSecretManager, OR accept that list_secrets() output may differ from input.

        This limitation is acceptable because:
        - EnvSecretManager is for LOCAL DEVELOPMENT ONLY (AC7)
        - Production uses Vault/AWS which don't have this ambiguity
        - get_secret() and set_secret() are unaffected

        Args:
            prefix: Optional filter prefix (e.g., "DATABASE_" returns only DB vars)
                   If None, returns ALL environment variables (⚠️ USE WITH CAUTION)

        Returns:
            List of environment variable names (e.g., ["DATABASE_PASSWORD", "DATABASE_HOST"])
            Returns ONLY names, NEVER values (AC12: secret redaction)

        Raises:
            SecretAccessError: Permission denied or environment access failure (rare)

        Security:
            - Returns secret NAMES only, NEVER values
            - ALWAYS use prefix filter in production to limit exposure
            - Only use prefix=None for debugging in isolated environments

        Example:
            >>> # ⚠️ DANGEROUS: Lists ALL environment variables (system + application)
            >>> all_secrets = secret_mgr.list_secrets()
            >>> print(all_secrets)
            ['PATH', 'HOME', 'AWS_ACCESS_KEY_ID', 'DATABASE_PASSWORD', 'ALPACA_API_KEY_ID']
            >>>
            >>> # ✓ SAFE: Lists only database secrets (scoped by prefix)
            >>> db_secrets = secret_mgr.list_secrets(prefix="DATABASE_")
            >>> print(db_secrets)
            ['DATABASE_PASSWORD', 'DATABASE_HOST', 'DATABASE_PORT']
            >>>
            >>> # ✓ SAFE: Lists only Alpaca trading secrets (scoped by prefix)
            >>> alpaca_secrets = secret_mgr.list_secrets(prefix="ALPACA_")
            >>> print(alpaca_secrets)
            ['ALPACA_API_KEY_ID', 'ALPACA_API_SECRET_KEY', 'ALPACA_BASE_URL']
        """
        with self._lock:
            # Runtime warning for list_secrets() without prefix (security best practice)
            if prefix is None:
                import warnings

                warnings.warn(
                    "list_secrets() called without prefix filter. "
                    "This returns ALL environment variables including system vars "
                    "(PATH, HOME, AWS credentials, etc.). "
                    "Recommendation: Use prefix filter (e.g., 'ALPACA_', 'DATABASE_') "
                    "to limit exposure. See AC12 for secret redaction requirements.",
                    category=UserWarning,
                    stacklevel=2,
                )

            try:
                env_vars = list(os.environ.keys())

                # Convert hierarchical prefix to env var format for filtering
                # "database/" -> "DATABASE_"
                if prefix is not None:
                    env_prefix = prefix.upper().replace("/", "_")
                    env_vars = [var for var in env_vars if var.startswith(env_prefix)]

                # Convert env var names back to hierarchical format
                # DATABASE_PASSWORD -> database/password
                # Note: This conversion is ambiguous (API_KEY_ID could be api/key/id or api_key/id)
                # but provides consistency with other backends
                hierarchical_names = [var.lower().replace("_", "/") for var in env_vars]

                logger.info(
                    "Listed environment variables",
                    extra={
                        "count": len(hierarchical_names),
                        "prefix": prefix,
                        "backend": "env",
                    },
                )
                return sorted(hierarchical_names)

            except Exception as e:
                raise SecretAccessError(
                    secret_name=f"list_secrets(prefix={prefix})",
                    backend="env",
                    reason=f"Failed to list environment variables: {e}",
                ) from e

    def set_secret(self, name: str, value: str) -> None:
        """
        Set or update an environment variable (runtime only, not persisted).

        This method updates os.environ and invalidates the cache. Changes are
        NOT persisted to .env file (runtime only). For permanent changes, edit
        .env file manually and restart the service.

        **WARNING**: Changes are lost on service restart. Edit .env file for
        permanent changes.

        Args:
            name: Hierarchical secret name (e.g., "database/password", "alpaca/api_key_id")
                 Converted to environment variable format (e.g., "DATABASE_PASSWORD", "ALPACA_API_KEY_ID")
            value: Secret value to set

        Returns:
            None (success indicated by no exception)

        Raises:
            SecretWriteError: Write operation failed (rare)

        Security:
            - NEVER log the secret value (AC12: secret redaction)
            - Log secret name + operation for audit trail
            - Invalidate cache after write (prevent stale cached values)

        Side Effects:
            - Updates os.environ (runtime environment only)
            - Invalidates in-memory cache for this secret
            - Logs audit entry (timestamp, secret name, operation=write)
            - NOT persisted to .env file (manual edit required)

        Example:
            >>> # Update secret (runtime only)
            >>> secret_mgr.set_secret("database/password", "new_password")
            >>> # Restart service to lose changes (edit .env for persistence)
        """
        with self._lock:
            try:
                # Convert hierarchical name to environment variable format
                # "database/password" -> "DATABASE_PASSWORD"
                env_var_name = name.upper().replace("/", "_")

                # Update environment variable
                os.environ[env_var_name] = value

                # Invalidate cache (force fresh fetch on next get)
                self._cache.invalidate(name)

                logger.info(
                    "Secret updated in environment",
                    extra={"secret_name": name, "backend": "env"},
                )

            except Exception as e:
                raise SecretWriteError(
                    secret_name=name,
                    backend="env",
                    reason=f"Failed to set environment variable: {e}",
                ) from e

    def close(self) -> None:
        """
        Clean up resources (clear cache).

        This method clears the in-memory cache and releases resources.
        Called automatically when used as context manager.

        Example:
            >>> secret_mgr = EnvSecretManager(dotenv_path=".env")
            >>> try:
            ...     db_password = secret_mgr.get_secret("DATABASE_PASSWORD")
            ... finally:
            ...     secret_mgr.close()  # Clear cache
        """
        self._cache.clear()
        logger.info(
            "EnvSecretManager closed, cache cleared",
            extra={"backend": "env"},
        )
