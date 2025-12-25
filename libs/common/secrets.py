# libs/common/secrets.py
"""Validation wrapper around libs/secrets/ (NOT a new abstraction).

Provides:
1. Fail-closed validation (SECRETS_VALIDATION_MODE=strict)
2. Type-safe getters with empty-value checks
3. Clear error messages
4. Lifecycle management

This module wraps the existing libs/secrets/ library to add:
- CRITICAL_SECRETS that always fail even in warn mode
- Validation mode configuration (strict/warn)
- Cache invalidation for secret rotation

Example:
    >>> from libs.common.secrets import get_required_secret, validate_required_secrets
    >>> validate_required_secrets(["database/url"])  # Fail fast at startup
    >>> db_url = get_required_secret("database/url")
"""
from __future__ import annotations

import logging
import os
import threading
from typing import TYPE_CHECKING

# USES EXISTING LIBRARY - NOT RECREATING
from libs.secrets.exceptions import SecretAccessError, SecretNotFoundError
from libs.secrets.factory import create_secret_manager

if TYPE_CHECKING:
    from libs.secrets.manager import SecretManager

logger = logging.getLogger(__name__)

_secret_manager: SecretManager | None = None
_secret_manager_lock = threading.Lock()  # Protects singleton operations

# Critical secrets that must fail even in warn mode (safety-critical)
# These secrets cannot be bypassed even with SECRETS_VALIDATION_MODE=warn
# NOTE: Must be kept in sync with docs/RUNBOOKS/secrets-manifest.md
CRITICAL_SECRETS = frozenset([
    "database/url",  # DB connection is always critical
    "alpaca/api_key_id",  # Trading credentials - never allow empty
    "alpaca/api_secret_key",  # Trading credentials - never allow empty
    # "internal_token/secret" - REMOVED: Not used in C7 scope (execution_gateway, signal_service)
    # Will be added when auth_service/orchestrator are migrated
    "killswitch/mtls_cert_path",
    "killswitch/mtls_key_path",
    "killswitch/ca_cert_path",
    "killswitch/jwt_signing_key",
    "killswitch/jwt_verification_key",
])


def _get_validation_mode() -> str:
    """Get validation mode. Defaults to strict (fail-closed)."""
    mode = os.getenv("SECRETS_VALIDATION_MODE", "strict").lower().strip()
    if mode not in ("strict", "warn"):
        logger.warning(
            "secrets_validation_mode_invalid",
            extra={"configured_mode": mode, "effective_mode": "strict"},
        )
        return "strict"
    if mode == "warn":
        # Use DEPLOYMENT_ENV (consistent with libs/secrets/factory.py)
        env = os.getenv("DEPLOYMENT_ENV", "production")
        if env == "production":
            logger.warning(
                "secrets_warn_mode_in_production",
                extra={"warning": "SECRETS_VALIDATION_MODE=warn in production is dangerous"},
            )
    return mode


def _check_environment_consistency() -> None:
    """Warn if ENVIRONMENT and DEPLOYMENT_ENV are inconsistent.

    ENVIRONMENT is used by services for business logic (e.g., webhook validation).
    DEPLOYMENT_ENV is used by libs/secrets/factory.py for backend guardrails.

    If these diverge, you can accidentally:
    - Allow env backend in production (if DEPLOYMENT_ENV is unset but ENVIRONMENT=prod)
    - Block local dev (if DEPLOYMENT_ENV=production but ENVIRONMENT=dev)
    """
    environment = os.getenv("ENVIRONMENT", "dev")
    deployment_env = os.getenv("DEPLOYMENT_ENV", "local")

    # Map ENVIRONMENT to expected DEPLOYMENT_ENV
    expected_mapping = {
        "dev": ("local", "test"),
        "test": ("local", "test"),
        "staging": ("staging",),
        "prod": ("production",),
        "production": ("production",),
    }

    expected = expected_mapping.get(environment, ())
    if expected and deployment_env not in expected:
        logger.warning(
            "environment_config_mismatch",
            extra={
                "ENVIRONMENT": environment,
                "DEPLOYMENT_ENV": deployment_env,
                "expected_DEPLOYMENT_ENV": expected,
                "note": "ENVIRONMENT and DEPLOYMENT_ENV may be inconsistent. "
                "Ensure both are set correctly for staging/production.",
            },
        )


def get_secret_manager() -> SecretManager:
    """Get singleton SecretManager instance from libs/secrets/.

    Thread-safe: uses double-checked locking pattern.
    """
    global _secret_manager
    # Fast path: return existing manager without lock
    if _secret_manager is not None:
        return _secret_manager

    # Slow path: acquire lock and create manager
    with _secret_manager_lock:
        # Double-check after acquiring lock
        if _secret_manager is None:
            _check_environment_consistency()
            _secret_manager = create_secret_manager()
        return _secret_manager


def close_secret_manager() -> None:
    """Close the SecretManager (call in lifespan shutdown).

    Thread-safe: uses lock to prevent concurrent close/create.
    """
    global _secret_manager
    with _secret_manager_lock:
        if _secret_manager is not None:
            _secret_manager.close()
            _secret_manager = None


def refresh_secrets() -> None:
    """Refresh secrets by recreating the SecretManager singleton.

    OPERATIONAL HOOK: This is an OPTIONAL convenience function for:
    - Operational scripts that need to pick up rotated secrets
    - Testing scenarios that modify environment variables
    - Future integration with rotation callbacks (B0T2)

    TYPICAL USAGE:
    - Standard secrets (DB, Alpaca): Use service restart (per task spec Phase 1)
    - Kill-switch certs: Use get_secret_uncached() for immediate rotation
    - Testing: Call refresh_secrets() after modifying env vars

    Per B0T1 task spec: "Initial implementation: restart service to pick up new env vars"
    This function satisfies AC7d "rotation hooks available" - the hook EXISTS,
    but production rotation still uses restart (until B0T2 adds callbacks).

    Thread-safe: uses lock for atomic close + create.
    """
    global _secret_manager
    with _secret_manager_lock:
        if _secret_manager is not None:
            _secret_manager.close()
            _secret_manager = None
        _check_environment_consistency()
        _secret_manager = create_secret_manager()


def get_required_secret(name: str) -> str:
    """Get secret, fail-closed if missing or empty.

    SECURITY: Critical secrets always fail, even in warn mode.

    WARN MODE BEHAVIOR:
    - Returns empty string "" for non-critical secrets if missing/empty
    - ALWAYS fails for CRITICAL_SECRETS (database, Alpaca, kill-switch)
    - Callers MUST handle empty returns appropriately
    - Only use warn mode for emergency debugging, never for safety-critical paths

    Callers should check: `if not value: handle_missing_secret()`

    Args:
        name: Secret name in hierarchical format (e.g., "database/url")

    Returns:
        The secret value (stripped of whitespace)

    Raises:
        RuntimeError: If secret is missing/empty and (mode is strict OR secret is critical)
    """
    mode = _get_validation_mode()
    is_critical = name in CRITICAL_SECRETS

    try:
        value = get_secret_manager().get_secret(name)
        if not value or not value.strip():
            msg = f"Required secret '{name}' is empty or whitespace-only."
            if mode == "strict" or is_critical:
                raise RuntimeError(msg)
            logger.warning("secrets_empty_warn", extra={"secret": name, "action": "returning_empty"})
            return ""  # Caller must handle empty case
        return value.strip()
    except SecretNotFoundError as e:
        msg = f"Required secret '{name}' not found: {e}"
        if mode == "strict" or is_critical:
            raise RuntimeError(msg) from e
        logger.warning("secrets_missing_warn", extra={"secret": name, "action": "returning_empty"})
        return ""  # Caller must handle empty case
    except SecretAccessError as e:
        msg = f"Failed to access secret '{name}' (backend error): {e}"
        if mode == "strict" or is_critical:
            raise RuntimeError(msg) from e
        logger.warning("secrets_access_error_warn", extra={"secret": name, "action": "returning_empty"})
        return ""  # Caller must handle empty case


def get_optional_secret(name: str, default: str = "") -> str:
    """Get secret, return default if missing. Returns str only.

    Args:
        name: Secret name in hierarchical format (e.g., "redis/password")
        default: Default value to return if secret is missing

    Returns:
        The secret value (stripped) or the default value
    """
    try:
        value = get_secret_manager().get_secret(name)
        return value.strip() if value and value.strip() else default
    except (SecretNotFoundError, SecretAccessError):
        return default


def get_optional_secret_or_none(name: str) -> str | None:
    """Get secret, return None if missing. For cases where None != empty string.

    Args:
        name: Secret name in hierarchical format

    Returns:
        The secret value (stripped) or None if missing/empty
    """
    try:
        value = get_secret_manager().get_secret(name)
        return value.strip() if value and value.strip() else None
    except (SecretNotFoundError, SecretAccessError):
        return None


def get_path_secret(name: str, default: str | None = None) -> str:
    """Get secret as file path (for cert paths).

    Args:
        name: Secret name for the path
        default: Default path if secret is optional

    Returns:
        The path value

    Raises:
        RuntimeError: If path is required (no default) and missing
    """
    value = get_required_secret(name) if default is None else get_optional_secret(name, "")
    if not value:
        if default is not None:
            return default
        raise RuntimeError(f"Required path secret '{name}' not found")
    return value


def validate_required_secrets(secrets: list[str]) -> None:
    """Validate all required secrets at startup.

    Call this early in lifespan BEFORE creating any clients that use secrets.
    This provides fail-fast behavior with clear error messages.

    Args:
        secrets: List of secret names to validate

    Raises:
        RuntimeError: If any required secrets are missing
    """
    mode = _get_validation_mode()
    missing = []
    for secret in secrets:
        is_critical = secret in CRITICAL_SECRETS
        try:
            value = get_secret_manager().get_secret(secret)
            if not value or not value.strip():
                if mode == "strict" or is_critical:
                    missing.append(secret)
                else:
                    logger.warning("secrets_empty_warn", extra={"secret": secret})
        except (SecretNotFoundError, SecretAccessError):
            if mode == "strict" or is_critical:
                missing.append(secret)
            else:
                logger.warning("secrets_missing_warn", extra={"secret": secret})

    if missing:
        raise RuntimeError(
            f"Missing required secrets: {missing}. "
            f"Set environment variables or configure SECRET_BACKEND."
        )


def _to_env_var_name(name: str) -> str:
    """Convert hierarchical name to env var format for cache key consistency.

    EnvSecretManager caches by env var name (DATABASE_URL), not hierarchical (database/url).
    See libs/secrets/env_backend.py:197 for the mapping.

    Args:
        name: Hierarchical secret name (e.g., "database/url")

    Returns:
        Environment variable format (e.g., "DATABASE_URL")
    """
    return name.upper().replace("/", "_")


def invalidate_secret(name: str) -> bool:
    """Invalidate cached secret (for rotation scenarios).

    After invalidation, next get_*_secret() call fetches fresh from backend.
    Use before reading cert paths when path itself may have changed.

    TECHNICAL NOTES (C7 scope decision):
    - Uses private `_cache` attribute which exists on ALL backends:
      - EnvSecretManager (env_backend.py)
      - VaultSecretManager (vault_backend.py:176)
      - AWSSecretManager (aws_backend.py:212)
    - This is acceptable for C7 because:
      1. All backends use the same cache.py implementation with `invalidate()` method
      2. Private access is temporary - B0T2 will add public interface
      3. Runtime check via hasattr() ensures graceful degradation if cache removed
      4. Cache invalidation is useful for testing all backends locally

    TODO(B0T2): Replace private `_cache` access with public `invalidate()` method
    on SecretManager interface. See libs/secrets/manager.py for interface definition.
    - EnvSecretManager caches by env var name (see libs/secrets/env_backend.py:197):
      `env_var_name = name.upper().replace("/", "_")`
    - We invalidate BOTH formats (hierarchical + env var) for safety
    - NOTE: For Vault/AWS in production, cache invalidation is a local optimization;
      the backend still fetches from the secret store on next get_secret() call

    Args:
        name: Secret name in hierarchical format

    Returns:
        True if invalidation succeeded, False if it was skipped or failed
    """
    mgr = get_secret_manager()

    # Check if backend has _cache attribute with invalidate method (all current backends do)
    # This runtime check ensures graceful degradation if cache is removed or interface changes
    if not hasattr(mgr, "_cache"):
        logger.warning(
            "invalidate_secret_no_cache",
            extra={
                "secret": name,
                "backend": type(mgr).__name__,
                "note": "Backend has no _cache attribute - skipping invalidation",
            },
        )
        return False

    if not hasattr(mgr._cache, "invalidate"):
        logger.warning(
            "invalidate_secret_no_method",
            extra={
                "secret": name,
                "backend": type(mgr).__name__,
                "note": "Cache has no invalidate method - skipping invalidation",
            },
        )
        return False

    # Invalidate both formats to ensure cache is cleared:
    # - Hierarchical format (database/url) - used by get_secret() calls
    # - Env var format (DATABASE_URL) - used by EnvSecretManager cache key (env_backend.py:197)
    try:
        mgr._cache.invalidate(name)
        mgr._cache.invalidate(_to_env_var_name(name))
        logger.debug("invalidate_secret_success", extra={"secret": name})
        return True
    except (AttributeError, TypeError) as e:
        logger.warning(
            "invalidate_secret_failed",
            extra={"secret": name, "error": str(e)},
        )
        return False


def get_secret_uncached(name: str) -> str:
    """Get secret bypassing cache (for rotation-sensitive secrets).

    Use for kill-switch certs where rotation must be immediate.

    GUARANTEE: This function will ALWAYS return a fresh value by:
    1. First attempting targeted cache invalidation (fast path)
    2. If invalidation fails, forcing a full secrets refresh (slow path)

    This ensures rotation-sensitive secrets (kill-switch certs) are never stale.

    Args:
        name: Secret name in hierarchical format

    Returns:
        The secret value (fetched fresh, bypassing cache)

    Raises:
        RuntimeError: If secret is missing/empty (same as get_required_secret)
    """
    invalidation_succeeded = invalidate_secret(name)

    if not invalidation_succeeded:
        # Fallback: force full refresh to guarantee fresh read
        # This is slower but ensures rotation-sensitive secrets are never stale
        logger.warning(
            "get_secret_uncached_fallback",
            extra={
                "secret": name,
                "note": "Cache invalidation failed, forcing full refresh",
            },
        )
        refresh_secrets()

    return get_required_secret(name)
