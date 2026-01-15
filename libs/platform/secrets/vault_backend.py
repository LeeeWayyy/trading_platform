"""
HashiCorp Vault Secret Manager Backend.

This module implements VaultSecretManager, a production-ready secrets backend
that integrates with HashiCorp Vault via the hvac library. This backend is
designed for staging and production environments (AC7).

Architecture:
    - Uses hvac client for Vault KV v2 API
    - HTTP connection pooling with retry logic
    - Token-based authentication (initial implementation)
    - In-memory caching with 1-hour TTL (AC22: trading safety)
    - Thread-safe operations via threading.Lock
    - Path convention: namespace/category/key → kv/data/namespace/category/key
    - Automatic retries (3 attempts, exponential backoff) for transient failures

Security Considerations:
    - AC12: Secret values NEVER logged (only paths)
    - AC22: 1-hour TTL cache (prevents trading halt during Vault downtime)
    - Token stored in memory only (never persisted to disk)
    - All HTTP connections use TLS (verify=True by default)
    - Sealed vault detection and error reporting

Usage Example:
    >>> from libs.platform.secrets.vault_backend import VaultSecretManager
    >>> secret_mgr = VaultSecretManager(
    ...     vault_url="https://vault.company.com:8200",
    ...     token="s.abc123xyz",
    ...     mount_point="kv"
    ... )
    >>> db_password = secret_mgr.get_secret("database/password")
    >>> alpaca_key = secret_mgr.get_secret("alpaca/api_key_id")

Migration Path:
    1. Local development: Use EnvSecretManager with .env file
    2. Staging/Production: Migrate to VaultSecretManager
    3. See docs/RUNBOOKS/secrets-migration.md for migration procedures

See Also:
    - docs/ADRs/0017-secrets-management.md - Architecture decisions
    - docs/RUNBOOKS/secrets-migration.md - Migration guide
    - docs/RUNBOOKS/secret-rotation.md - Rotation procedures
"""

import logging
import threading
from datetime import timedelta

import hvac
from hvac.exceptions import (
    Forbidden,
    InvalidPath,
    InvalidRequest,
    Unauthorized,
    VaultDown,
    VaultError,
)
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from libs.platform.secrets.cache import SecretCache
from libs.platform.secrets.exceptions import (
    SecretAccessError,
    SecretManagerError,
    SecretNotFoundError,
    SecretWriteError,
)
from libs.platform.secrets.manager import SecretManager

logger = logging.getLogger(__name__)


class VaultSecretManager(SecretManager):
    """
    HashiCorp Vault secrets backend for staging and production.

    This implementation integrates with HashiCorp Vault via hvac library,
    providing enterprise-grade secrets management with versioning, auditing,
    and dynamic secrets support. It provides in-memory caching with 1-hour
    TTL for consistency with other backends (AC22).

    **Recommended for**: Staging and production environments (AC7).

    Features:
        - KV v2 secret engine support (versioned secrets)
        - Token-based authentication (VAULT_TOKEN env var or explicit token)
        - HTTP connection pooling with retries
        - In-memory caching with 1-hour TTL (AC22)
        - Thread-safe operations (threading.Lock)
        - TLS verification by default (verify=True)

    Thread Safety:
        All operations are protected by threading.Lock for concurrent access.

    Caching:
        - Secrets cached in-memory for 1 hour (AC22: trading safety)
        - Cache invalidated on set_secret() or service restart
        - No disk persistence (security requirement)

    Security:
        - AC12: Secret values NEVER logged (only paths)
        - AC7: Production-safe (auditing, versioning, access control)
        - Token stored in memory only (never persisted)
        - All HTTP connections use TLS (verify=True by default)

    Path Convention:
        - Input path: "database/password" (namespace/category/key)
        - Vault KV v2 path: "kv/data/database/password"
        - Mount point configurable (default: "kv")

    Example:
        >>> # Basic usage with token
        >>> secret_mgr = VaultSecretManager(
        ...     vault_url="https://vault.company.com:8200",
        ...     token="s.abc123xyz",
        ...     mount_point="kv"
        ... )
        >>> db_password = secret_mgr.get_secret("database/password")
        >>>
        >>> # List all secrets with prefix
        >>> db_secrets = secret_mgr.list_secrets(prefix="database/")
        >>> print(db_secrets)
        ['database/password', 'database/host', 'database/port']
        >>>
        >>> # Update secret (creates new version)
        >>> secret_mgr.set_secret("database/password", "new_password")
        >>>
        >>> # Context manager usage
        >>> with VaultSecretManager(vault_url=url, token=token) as secret_mgr:
        ...     db_password = secret_mgr.get_secret("database/password")
    """

    def __init__(
        self,
        vault_url: str,
        token: str | None = None,
        mount_point: str = "kv",
        cache_ttl_seconds: int = 3600,
        verify: bool = True,
    ) -> None:
        """
        Initialize VaultSecretManager with Vault connection parameters.

        Args:
            vault_url: Vault server URL (e.g., "https://vault.company.com:8200")
            token: Vault token for authentication. If None, reads from VAULT_TOKEN env var.
                  WARNING: Never hardcode tokens in source code - use env vars or CI secrets.
            mount_point: KV secret engine mount point. Default: "kv"
            cache_ttl_seconds: Cache TTL in seconds. Default: 3600 (1 hour)
                              Set to 0 to disable caching.
            verify: Verify TLS certificates. Default: True (RECOMMENDED)
                   Set to False only for local development with self-signed certs.

        Raises:
            SecretAccessError: Vault connection failure, authentication failure, or sealed vault
                - Verify vault_url is correct and accessible
                - Check token is valid and not expired
                - Ensure Vault is unsealed (vault status)
                - Check network connectivity and firewall rules

        Example:
            >>> # Production: Token from environment variable
            >>> import os
            >>> secret_mgr = VaultSecretManager(
            ...     vault_url="https://vault.company.com:8200",
            ...     token=os.environ.get("VAULT_TOKEN")
            ... )
            >>>
            >>> # Local development: Self-signed cert (NOT for production)
            >>> secret_mgr = VaultSecretManager(
            ...     vault_url="https://localhost:8200",
            ...     token="s.dev_token",
            ...     verify=False  # Only for local dev
            ... )
        """
        self._lock = threading.Lock()
        self._cache = SecretCache(ttl=timedelta(seconds=cache_ttl_seconds))
        self._vault_url = vault_url
        self._mount_point = mount_point
        self._verify = verify

        # Initialize hvac client
        try:
            self._client = hvac.Client(
                url=vault_url,
                token=token,
                verify=verify,
            )

            # Verify connectivity and authentication
            # Note: is_authenticated() requires 'lookup-self' capability.
            # Some production tokens intentionally omit this, so we handle
            # Forbidden gracefully by assuming the token is valid.
            try:
                if not self._client.is_authenticated():
                    raise SecretAccessError(
                        secret_name="vault_auth",
                        backend="vault",
                        reason=(
                            f"Vault authentication failed for {vault_url}. "
                            f"Verify token is valid and not expired."
                        ),
                    )
            except Forbidden:
                # Token lacks 'lookup-self' capability but may still work for
                # secret operations. Defer validation to first secret access.
                logger.info(
                    "Vault token lacks 'lookup-self' capability, deferring validation",
                    extra={"vault_url": vault_url, "backend": "vault"},
                )

            # Check if Vault is sealed (use proper hvac API)
            # Note: sys.is_sealed() requires 'sys/seal-status' capability.
            # Some production tokens intentionally omit this, so we handle
            # Forbidden gracefully by skipping the seal check.
            try:
                if self._client.sys.is_sealed():
                    raise SecretAccessError(
                        secret_name="vault_status",
                        backend="vault",
                        reason=(
                            f"Vault is sealed at {vault_url}. "
                            f"Unseal Vault before accessing secrets (vault operator unseal)."
                        ),
                    )
            except Forbidden:
                # Token lacks 'sys/seal-status' capability but may still work for
                # secret operations. Skip seal check and defer validation to first secret access.
                logger.info(
                    "Vault token lacks 'sys/seal-status' capability, skipping seal check",
                    extra={"vault_url": vault_url, "backend": "vault"},
                )

            logger.info(
                "Connected to Vault successfully",
                extra={
                    "vault_url": vault_url,
                    "mount_point": mount_point,
                    "backend": "vault",
                },
            )

        except SecretManagerError:
            # Re-raise intentional errors (auth failure, sealed vault) with original metadata
            raise
        except (Unauthorized, Forbidden) as e:
            raise SecretAccessError(
                secret_name="vault_auth",
                backend="vault",
                reason=f"Vault authentication failed: {e}",
            ) from e
        except VaultDown as e:
            raise SecretAccessError(
                secret_name="vault_connectivity",
                backend="vault",
                reason=f"Vault server unreachable at {vault_url}: {e}",
            ) from e
        except VaultError as e:
            logger.error(
                "Vault initialization failed - server error",
                extra={
                    "vault_url": vault_url,
                    "backend": "vault",
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )
            raise SecretAccessError(
                secret_name="vault_init",
                backend="vault",
                reason=f"Vault initialization failed: {e}",
            ) from e
        except (ValueError, TypeError) as e:
            logger.error(
                "Vault initialization failed - invalid configuration",
                extra={
                    "vault_url": vault_url,
                    "backend": "vault",
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
                exc_info=True,
            )
            raise SecretAccessError(
                secret_name="vault_init",
                backend="vault",
                reason=f"Invalid Vault configuration: {e}",
            ) from e

    def get_secret(self, name: str) -> str:
        """
        Retrieve a secret from Vault KV v2 secret engine.

        This method checks the in-memory cache first, then fetches from Vault
        if cache miss or expired. Secret values are cached for 1 hour
        (configurable via cache_ttl_seconds).

        Args:
            name: Secret path in Vault (e.g., "database/password", "alpaca/api_key_id")
                 Format: "namespace/category/key"
                 Vault KV v2 path: "{mount_point}/data/{name}"

        Returns:
            Secret value as string (most recent version from KV v2)

        Raises:
            SecretNotFoundError: Secret doesn't exist at path
                - Verify secret exists: `vault kv get {mount_point}/{name}`
                - Check path spelling and namespace
                - Review secret naming conventions

            SecretAccessError: Vault connectivity or permission failure
                - Network timeout (Vault unreachable)
                - Permission denied (insufficient ACL policy)
                - Vault sealed (vault operator unseal)
                - Token expired (renew token)

        Multi-Key Secrets:
            Vault KV v2 secrets can contain multiple key-value pairs.
            This method uses the following fallback logic:

            1. If "value" key exists: Returns secret_data["value"]
            2. Otherwise: Returns first key alphabetically (deterministic)

            **Recommendation**: Always use "value" key for single-value secrets
            to ensure consistent behavior across all backends (AWS, Vault, Env).

            Example Vault secret with multiple keys:
                {
                    "value": "secret123",      # ✓ Preferred: This will be returned
                    "username": "admin",
                    "api_key": "xyz789"
                }

            Example Vault secret without "value" key:
                {
                    "api_key": "xyz789",       # ✓ Will be returned (first alphabetically)
                    "username": "admin"
                }

        Security:
            - NEVER log the returned secret value (AC12)
            - Log secret path for audit trail
            - Cache invalidated after cache_ttl_seconds

        Performance:
            - First call: ~50-100ms (HTTP fetch from Vault)
            - Cached calls: <1ms (in-memory lookup)
            - Cache TTL: 1 hour (default)

        Example:
            >>> db_password = secret_mgr.get_secret("database/password")
            >>> logger.info(f"Loaded secret: database/password")  # CORRECT: Path only
            >>> # print(db_password)  # WRONG: Exposes secret in logs
        """
        try:
            return self._get_secret_with_retry(name)
        except VaultDown as e:
            logger.error(
                "Vault unreachable after retries",
                extra={"secret_path": name, "backend": "vault"},
            )
            raise SecretAccessError(
                secret_name=name,
                backend="vault",
                reason=f"Vault server unreachable: {e}",
            ) from e

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type(VaultDown),
        reraise=True,
    )
    def _get_secret_with_retry(self, name: str) -> str:
        with self._lock:
            # Check cache first
            cached_value = self._cache.get(name)
            if cached_value is not None:
                logger.debug(
                    "Secret cache hit",
                    extra={"secret_path": name, "backend": "vault"},
                )
                return cached_value

            # Cache miss - fetch from Vault
            try:
                # KV v2 API: mount_point/data/path
                response = self._client.secrets.kv.v2.read_secret_version(
                    path=name,
                    mount_point=self._mount_point,
                )

                # Extract secret value from response
                # KV v2 response structure: {data: {data: {key: value}}}
                secret_data = response.get("data", {}).get("data", {})

                if not secret_data:
                    raise SecretNotFoundError(
                        secret_name=name,
                        backend="vault",
                        additional_context=(
                            f"Secret '{name}' exists but has no data. "
                            f"Verify secret was written correctly: "
                            f"vault kv get {self._mount_point}/{name}"
                        ),
                    )

                # For secrets with multiple keys, we need a convention
                # Default: Use "value" key, or first key if "value" doesn't exist
                if "value" in secret_data:
                    value = str(secret_data["value"])
                else:
                    # Use first key (alphabetically sorted for determinism)
                    first_key = sorted(secret_data.keys())[0]
                    value = str(secret_data[first_key])
                    logger.debug(
                        "Secret has multiple keys, using first key",
                        extra={
                            "secret_path": name,
                            "key_used": first_key,
                            "backend": "vault",
                        },
                    )

                # Cache the value
                self._cache.set(name, value)
                logger.info(
                    "Secret loaded from Vault",
                    extra={"secret_path": name, "backend": "vault"},
                )
                return value

            except SecretNotFoundError:
                # Re-raise intentional errors (empty data, invalid path) with original metadata
                raise
            except InvalidPath as e:
                raise SecretNotFoundError(
                    secret_name=name,
                    backend="vault",
                    additional_context=(
                        f"Secret path '{name}' not found in Vault. "
                        f"Verify path: vault kv get {self._mount_point}/{name}"
                    ),
                ) from e
            except Forbidden as e:
                raise SecretAccessError(
                    secret_name=name,
                    backend="vault",
                    reason=(
                        f"Permission denied reading '{name}'. "
                        f"Verify token has read access to {self._mount_point}/{name}"
                    ),
                ) from e
            except VaultDown:
                # Re-raise VaultDown to allow retry decorator to handle it
                # (VaultDown is a subclass of VaultError, so must be caught first)
                raise
            except VaultError as e:
                logger.error(
                    "Vault secret read failed - server error",
                    extra={
                        "secret_name": name,
                        "backend": "vault",
                        "error": str(e),
                        "error_type": type(e).__name__,
                    },
                    exc_info=True,
                )
                raise SecretAccessError(
                    secret_name=name,
                    backend="vault",
                    reason=f"Vault error reading '{name}': {e}",
                ) from e
            except (KeyError, ValueError) as e:
                logger.error(
                    "Vault secret read failed - invalid response format",
                    extra={
                        "secret_name": name,
                        "backend": "vault",
                        "error": str(e),
                        "error_type": type(e).__name__,
                    },
                    exc_info=True,
                )
                raise SecretAccessError(
                    secret_name=name,
                    backend="vault",
                    reason=f"Invalid secret data format for '{name}': {e}",
                ) from e
            except RuntimeError as e:
                logger.error(
                    "Vault secret read failed - runtime error",
                    extra={
                        "secret_name": name,
                        "backend": "vault",
                        "error": str(e),
                        "error_type": "RuntimeError",
                    },
                    exc_info=True,
                )
                raise SecretAccessError(
                    secret_name=name,
                    backend="vault",
                    reason=f"Unexpected runtime error reading '{name}': {e}",
                ) from e

    def list_secrets(self, prefix: str | None = None) -> list[str]:
        """
        List all secret paths in Vault KV v2 engine (optional prefix filter).

        This method lists all secrets under the mount point, optionally filtered
        by prefix. Useful for verification and debugging.

        **Note**: Listing requires separate ACL policy (list capability).

        Args:
            prefix: Optional filter prefix (e.g., "database/" returns only DB secrets)
                   If None, returns all secrets under mount point

        Returns:
            List of secret paths (e.g., ["database/password", "alpaca/api_key_id"])
            Returns ONLY paths, NEVER values (AC12: secret redaction)

        Raises:
            SecretAccessError: Permission denied, Vault unreachable, or listing not supported
                - Verify token has list capability: vault token capabilities {mount_point}/metadata
                - Check Vault ACL policy includes: path "{mount_point}/metadata/*" { capabilities = ["list"] }
                - Ensure mount point exists and is KV v2

        Security:
            - Returns secret PATHS only, NEVER values
            - Requires separate ACL policy (list capability)

        Example:
            >>> # List all secrets
            >>> all_secrets = secret_mgr.list_secrets()
            >>> print(all_secrets)
            ['database/password', 'alpaca/api_key_id', 'alpaca/api_secret_key']
            >>>
            >>> # List only database secrets
            >>> db_secrets = secret_mgr.list_secrets(prefix="database/")
            >>> print(db_secrets)
            ['database/password', 'database/host', 'database/port']
        """
        try:
            return self._list_secrets_with_retry(prefix)
        except VaultDown as e:
            logger.error(
                "Vault unreachable during list_secrets",
                extra={"prefix": prefix, "backend": "vault"},
            )
            raise SecretAccessError(
                secret_name=f"list_secrets(prefix={prefix})",
                backend="vault",
                reason=f"Vault server unreachable: {e}",
            ) from e

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type(VaultDown),
        reraise=True,
    )
    def _list_secrets_with_retry(self, prefix: str | None = None) -> list[str]:
        with self._lock:
            try:
                # Iterative implementation using stack to prevent stack overflow
                # with deeply nested secret hierarchies
                all_paths: list[str] = []
                stack = [prefix.rstrip("/") if prefix else ""]

                while stack:
                    current_path = stack.pop()

                    # KV v2 list API: mount_point/metadata/path
                    response = self._client.secrets.kv.v2.list_secrets(
                        path=current_path,
                        mount_point=self._mount_point,
                    )

                    # Extract keys from response
                    # KV v2 list response structure: {data: {keys: [...]}}
                    keys = response.get("data", {}).get("keys", [])

                    # Build full paths by combining current path with keys
                    for key in keys:
                        if current_path:
                            full_path = f"{current_path}/{key}".rstrip("/")
                        else:
                            full_path = key.rstrip("/")

                        if key.endswith("/"):
                            # Directory - add to stack for processing
                            stack.append(full_path)
                        else:
                            # Secret - add to results
                            all_paths.append(full_path)

                logger.info(
                    "Listed Vault secrets",
                    extra={
                        "count": len(all_paths),
                        "prefix": prefix,
                        "backend": "vault",
                    },
                )
                return sorted(all_paths)

            except InvalidPath:
                # Empty directory or non-existent prefix
                logger.info(
                    "No secrets found with prefix",
                    extra={"prefix": prefix, "backend": "vault"},
                )
                return []
            except Forbidden as e:
                raise SecretAccessError(
                    secret_name=f"list_secrets(prefix={prefix})",
                    backend="vault",
                    reason=(
                        f"Permission denied listing secrets. "
                        f"Verify token has list capability on {self._mount_point}/metadata/*"
                    ),
                ) from e
            except VaultDown:
                # Re-raise VaultDown to allow retry decorator to handle it
                # (VaultDown is a subclass of VaultError, so must be caught first)
                raise
            except VaultError as e:
                logger.error(
                    "Vault secret listing failed - server error",
                    extra={
                        "prefix": prefix,
                        "backend": "vault",
                        "error": str(e),
                        "error_type": type(e).__name__,
                    },
                    exc_info=True,
                )
                raise SecretAccessError(
                    secret_name=f"list_secrets(prefix={prefix})",
                    backend="vault",
                    reason=f"Vault error listing secrets: {e}",
                ) from e
            except (KeyError, ValueError, TypeError) as e:
                logger.error(
                    "Vault secret listing failed - invalid response format",
                    extra={
                        "prefix": prefix,
                        "backend": "vault",
                        "error": str(e),
                        "error_type": type(e).__name__,
                    },
                    exc_info=True,
                )
                raise SecretAccessError(
                    secret_name=f"list_secrets(prefix={prefix})",
                    backend="vault",
                    reason=f"Invalid response format listing secrets: {e}",
                ) from e
            except RuntimeError as e:
                logger.error(
                    "Vault secret listing failed - runtime error",
                    extra={
                        "prefix": prefix,
                        "backend": "vault",
                        "error": str(e),
                        "error_type": "RuntimeError",
                    },
                    exc_info=True,
                )
                raise SecretAccessError(
                    secret_name=f"list_secrets(prefix={prefix})",
                    backend="vault",
                    reason=f"Unexpected runtime error listing secrets: {e}",
                ) from e

    def set_secret(self, name: str, value: str) -> None:
        """
        Write or update a secret in Vault KV v2 engine (creates new version).

        This method creates a new secret or updates an existing one, creating
        a new version in Vault's version history. Existing versions are preserved
        for audit and rollback.

        **Note**: Writing requires separate ACL policy (create/update capability).

        Args:
            name: Secret path in Vault (same format as get_secret)
            value: Secret value to store (stored as {"value": <value>} in KV v2)

        Returns:
            None (success indicated by no exception)

        Raises:
            SecretWriteError: Write operation failed
                - Insufficient permissions (create/update capability required)
                - Vault in standby mode (redirect to active node)
                - Network failure during write

            SecretAccessError: Vault connectivity or authentication failure
                - Token expired (renew token)
                - Vault sealed (vault operator unseal)

        Security:
            - NEVER log the secret value (AC12: secret redaction)
            - Log secret path + operation for audit trail
            - Invalidate cache after write (prevent stale cached values)

        Side Effects:
            - Creates new version in Vault (version history preserved)
            - Invalidates in-memory cache for this secret (forces fresh fetch)
            - Logs audit entry (timestamp, path, operation=write)
            - Vault audit log records full operation (if enabled)

        Example:
            >>> # Zero-downtime rotation workflow
            >>> new_password = generate_secure_password()
            >>> secret_mgr.set_secret("database/password", new_password)
            >>> # Cache invalidated, next get_secret() fetches new version
        """
        try:
            self._set_secret_with_retry(name, value)
        except VaultDown as e:
            logger.error(
                "Vault unreachable during secret write",
                extra={"secret_path": name, "backend": "vault"},
            )
            raise SecretAccessError(
                secret_name=name,
                backend="vault",
                reason=f"Vault server unreachable during write: {e}",
            ) from e

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type(VaultDown),
        reraise=True,
    )
    def _set_secret_with_retry(self, name: str, value: str) -> None:
        with self._lock:
            try:
                # KV v2 write API: mount_point/data/path
                # Store as {"value": <value>} for consistency with get_secret
                self._client.secrets.kv.v2.create_or_update_secret(
                    path=name,
                    secret={"value": value},
                    mount_point=self._mount_point,
                )

                # Invalidate cache (force fresh fetch on next get)
                self._cache.invalidate(name)

                logger.info(
                    "Secret written to Vault",
                    extra={"secret_path": name, "backend": "vault"},
                )

            except Forbidden as e:
                raise SecretWriteError(
                    secret_name=name,
                    backend="vault",
                    reason=(
                        f"Permission denied writing '{name}'. "
                        f"Verify token has create/update capability on {self._mount_point}/{name}"
                    ),
                ) from e
            except InvalidRequest as e:
                raise SecretWriteError(
                    secret_name=name,
                    backend="vault",
                    reason=f"Invalid write request for '{name}': {e}",
                ) from e
            except VaultDown:
                # Re-raise VaultDown to allow retry decorator to handle it
                # (VaultDown is a subclass of VaultError, so must be caught first)
                raise
            except VaultError as e:
                logger.error(
                    "Vault secret write failed - server error",
                    extra={
                        "secret_name": name,
                        "backend": "vault",
                        "error": str(e),
                        "error_type": type(e).__name__,
                    },
                    exc_info=True,
                )
                raise SecretWriteError(
                    secret_name=name,
                    backend="vault",
                    reason=f"Vault error writing '{name}': {e}",
                ) from e
            except (ValueError, TypeError) as e:
                logger.error(
                    "Vault secret write failed - invalid value",
                    extra={
                        "secret_name": name,
                        "backend": "vault",
                        "error": str(e),
                        "error_type": type(e).__name__,
                    },
                    exc_info=True,
                )
                raise SecretWriteError(
                    secret_name=name,
                    backend="vault",
                    reason=f"Invalid secret value for '{name}': {e}",
                ) from e
            except RuntimeError as e:
                logger.error(
                    "Vault secret write failed - runtime error",
                    extra={
                        "secret_name": name,
                        "backend": "vault",
                        "error": str(e),
                        "error_type": "RuntimeError",
                    },
                    exc_info=True,
                )
                raise SecretWriteError(
                    secret_name=name,
                    backend="vault",
                    reason=f"Unexpected runtime error writing '{name}': {e}",
                ) from e

    def close(self) -> None:
        """
        Clean up resources (clear cache, close HTTP adapter).

        This method clears the in-memory cache and closes the hvac client's
        HTTP adapter (connection pool). Called automatically when used as
        context manager.

        Example:
            >>> secret_mgr = VaultSecretManager(vault_url=url, token=token)
            >>> try:
            ...     db_password = secret_mgr.get_secret("database/password")
            ... finally:
            ...     secret_mgr.close()  # Clear cache and close connections
        """
        self._cache.clear()
        # Close hvac client's HTTP adapter (connection pool)
        with self._lock:
            adapter = getattr(self._client, "adapter", None)
            if adapter and hasattr(adapter, "close"):
                adapter.close()
        logger.info(
            "VaultSecretManager closed, cache cleared",
            extra={"backend": "vault"},
        )
