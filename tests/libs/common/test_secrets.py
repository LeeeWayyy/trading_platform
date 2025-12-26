# tests/libs/common/test_secrets.py
"""Tests for libs/common/secrets.py validation wrapper.

Tests cover all AC7 acceptance criteria:
- AC7b: Validation fails startup on missing required secrets
- AC7c: Clear error messages for missing secrets
- AC7d: Rotation hooks available (refresh_secrets, get_secret_uncached)
- AC7e: Environment completeness checklist

Test categories:
1. Missing/empty secret validation (strict mode)
2. Critical secrets enforcement (always fail, even in warn mode)
3. Error message clarity
4. Rotation hooks (refresh_secrets, get_secret_uncached, invalidate_secret)
5. Validation modes (strict/warn/invalid)
6. Factory guardrails (env backend restrictions)
7. Conditional requirements (DRY_RUN, ENVIRONMENT)
8. Import-time safety (no secrets at import)
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from libs.common import secrets
from libs.secrets.exceptions import SecretAccessError

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture(autouse=True)
def reset_secret_manager() -> Generator[None, None, None]:
    """Reset singleton between tests.

    CRITICAL: This fixture ensures each test starts with a clean slate.
    Without this, cached secrets from previous tests leak into subsequent tests.
    """
    secrets._secret_manager = None
    yield
    # Only call close on real secret managers, not mocks
    if secrets._secret_manager is not None and hasattr(secrets._secret_manager, "close"):
        try:
            secrets._secret_manager.close()
        except (AttributeError, TypeError):
            pass  # Mock doesn't have close method
    secrets._secret_manager = None


@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove all secret-related env vars for clean test state."""
    secret_vars = [
        "DATABASE_URL",
        "ALPACA_API_KEY_ID",
        "ALPACA_API_SECRET_KEY",
        "REDIS_PASSWORD",
        "WEBHOOK_SECRET",
        "KILLSWITCH_MTLS_CERT_PATH",
        "KILLSWITCH_MTLS_KEY_PATH",
        "KILLSWITCH_CA_CERT_PATH",
        "KILLSWITCH_JWT_SIGNING_KEY",
        "KILLSWITCH_JWT_VERIFICATION_KEY",
        "SECRETS_VALIDATION_MODE",
        "SECRET_BACKEND",
        "DEPLOYMENT_ENV",
    ]
    for var in secret_vars:
        monkeypatch.delenv(var, raising=False)


# ============================================================================
# AC7b: Missing/Empty Secret Validation (Strict Mode)
# ============================================================================


def test_missing_required_secret_fails_startup_strict_mode(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """AC7b Test 1: Missing required secret fails in strict mode."""
    monkeypatch.setenv("SECRETS_VALIDATION_MODE", "strict")
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")
    # DATABASE_URL not set

    with pytest.raises(RuntimeError, match="Required secret 'database/url' not found"):
        secrets.get_required_secret("database/url")


def test_empty_secret_value_fails_validation_strict_mode(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """AC7b Test 2: Empty secret value fails in strict mode."""
    monkeypatch.setenv("SECRETS_VALIDATION_MODE", "strict")
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", "")  # Empty value

    with pytest.raises(RuntimeError, match="Required secret 'database/url' is empty"):
        secrets.get_required_secret("database/url")


def test_whitespace_only_secret_fails_validation(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """AC7b Test 2b: Whitespace-only secret value fails validation."""
    monkeypatch.setenv("SECRETS_VALIDATION_MODE", "strict")
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", "   \t\n  ")  # Whitespace only

    with pytest.raises(
        RuntimeError, match="Required secret 'database/url' is empty or whitespace-only"
    ):
        secrets.get_required_secret("database/url")


def test_validate_required_secrets_fails_on_missing(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """AC7b Test 1b: validate_required_secrets fails startup on missing secrets."""
    monkeypatch.setenv("SECRETS_VALIDATION_MODE", "strict")
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")
    # No secrets set

    # Verify error message includes both missing secrets and setup hint
    with pytest.raises(RuntimeError, match="Missing required secrets") as excinfo:
        secrets.validate_required_secrets(["database/url", "alpaca/api_key_id"])

    error_msg = str(excinfo.value)
    assert "database/url" in error_msg
    assert "alpaca/api_key_id" in error_msg
    assert "Set environment variables or configure SECRET_BACKEND" in error_msg


# ============================================================================
# AC7c: Error Message Clarity
# ============================================================================


def test_error_messages_include_secret_name(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """AC7c Test 3: Error messages include secret name for debugging."""
    monkeypatch.setenv("SECRETS_VALIDATION_MODE", "strict")
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")

    # Test 1: Missing secret
    with pytest.raises(RuntimeError) as exc_info:
        secrets.get_required_secret("database/url")
    assert "database/url" in str(exc_info.value)

    # Test 2: Empty secret
    monkeypatch.setenv("ALPACA_API_KEY_ID", "")
    with pytest.raises(RuntimeError) as exc_info:
        secrets.get_required_secret("alpaca/api_key_id")
    assert "alpaca/api_key_id" in str(exc_info.value)


def test_backend_error_includes_secret_name(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """AC7c Test 3b: Backend access errors include secret name."""
    monkeypatch.setenv("SECRETS_VALIDATION_MODE", "strict")
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")

    # Mock backend raising SecretAccessError (requires secret_name, backend, reason)
    mock_mgr = MagicMock()
    mock_mgr.get_secret.side_effect = SecretAccessError(
        secret_name="database/url", backend="env", reason="Backend unavailable"
    )
    secrets._secret_manager = mock_mgr

    with pytest.raises(RuntimeError) as exc_info:
        secrets.get_required_secret("database/url")

    error_msg = str(exc_info.value)
    assert "database/url" in error_msg
    assert "backend error" in error_msg.lower()


# ============================================================================
# AC7b: Critical Secrets Always Fail (Even in Warn Mode)
# ============================================================================


def test_critical_secrets_always_fail_warn_mode(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """AC7b Test 6: Critical secrets fail even in warn mode."""
    monkeypatch.setenv("SECRETS_VALIDATION_MODE", "warn")
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")

    # All critical secrets should fail in warn mode
    critical_tests = [
        "database/url",
        "alpaca/api_key_id",
        "alpaca/api_secret_key",
        "killswitch/mtls_cert_path",
        "killswitch/mtls_key_path",
        "killswitch/ca_cert_path",
        "killswitch/jwt_signing_key",
        "killswitch/jwt_verification_key",
    ]

    for secret_name in critical_tests:
        with pytest.raises(RuntimeError, match=f"Required secret '{secret_name}'"):
            secrets.get_required_secret(secret_name)


def test_critical_secrets_empty_value_fails_warn_mode(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """AC7b Test 6b: Critical secrets with empty values fail in warn mode."""
    monkeypatch.setenv("SECRETS_VALIDATION_MODE", "warn")
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")

    # Set critical secret to empty value
    monkeypatch.setenv("DATABASE_URL", "")

    with pytest.raises(RuntimeError, match="Required secret 'database/url' is empty"):
        secrets.get_required_secret("database/url")


def test_validate_required_secrets_critical_always_fail_warn_mode(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """AC7b Test 6c: validate_required_secrets fails for critical secrets in warn mode."""
    monkeypatch.setenv("SECRETS_VALIDATION_MODE", "warn")
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")

    # Critical secrets must fail validation even in warn mode
    with pytest.raises(RuntimeError, match="Missing required secrets"):
        secrets.validate_required_secrets(["database/url", "alpaca/api_key_id"])


# ============================================================================
# Warn Mode Behavior (Non-Critical Secrets)
# ============================================================================


def test_warn_mode_returns_empty_for_non_critical_missing(
    monkeypatch: pytest.MonkeyPatch, clean_env: None, caplog: pytest.LogCaptureFixture
) -> None:
    """Warn mode returns empty string for non-critical missing secrets."""
    monkeypatch.setenv("SECRETS_VALIDATION_MODE", "warn")
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")

    result = secrets.get_required_secret("redis/password")
    assert result == ""
    assert "secrets_missing_warn" in caplog.text


def test_warn_mode_returns_empty_for_non_critical_empty(
    monkeypatch: pytest.MonkeyPatch, clean_env: None, caplog: pytest.LogCaptureFixture
) -> None:
    """Warn mode returns empty string for non-critical empty secrets."""
    monkeypatch.setenv("SECRETS_VALIDATION_MODE", "warn")
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")
    monkeypatch.setenv("REDIS_PASSWORD", "")

    result = secrets.get_required_secret("redis/password")
    assert result == ""
    assert "secrets_empty_warn" in caplog.text


def test_warn_mode_in_production_logs_warning(
    monkeypatch: pytest.MonkeyPatch, clean_env: None, caplog: pytest.LogCaptureFixture
) -> None:
    """Warn mode in production environment logs warning."""
    monkeypatch.setenv("SECRETS_VALIDATION_MODE", "warn")
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "production")
    monkeypatch.setenv("DATABASE_URL", "valid_url")

    # Trigger validation mode check
    secrets._get_validation_mode()
    assert "secrets_warn_mode_in_production" in caplog.text


def test_invalid_validation_mode_defaults_to_strict(
    monkeypatch: pytest.MonkeyPatch, clean_env: None, caplog: pytest.LogCaptureFixture
) -> None:
    """Invalid SECRETS_VALIDATION_MODE defaults to strict."""
    monkeypatch.setenv("SECRETS_VALIDATION_MODE", "invalid_mode")
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")

    mode = secrets._get_validation_mode()
    assert mode == "strict"
    assert "secrets_validation_mode_invalid" in caplog.text


# ============================================================================
# AC7d: Rotation Hooks (refresh_secrets, invalidate_secret, get_secret_uncached)
# ============================================================================


def test_refresh_secrets_can_be_invoked(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """AC7d Test 4: Rotation hook refresh_secrets() can be invoked."""
    monkeypatch.setenv("SECRETS_VALIDATION_MODE", "strict")
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", "original_value")

    # First read
    value1 = secrets.get_required_secret("database/url")
    assert value1 == "original_value"

    # Invoke refresh (should not raise)
    secrets.refresh_secrets()

    # Can still read secrets after refresh
    value2 = secrets.get_required_secret("database/url")
    assert value2 == "original_value"


def test_refresh_secrets_picks_up_new_values(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """AC7d Test 27: refresh_secrets() picks up rotated values."""
    monkeypatch.setenv("SECRETS_VALIDATION_MODE", "strict")
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", "old_value")

    # First read caches the value
    value1 = secrets.get_required_secret("database/url")
    assert value1 == "old_value"

    # Update env var (simulating secret rotation)
    monkeypatch.setenv("DATABASE_URL", "new_value")

    # Without refresh, cached value returned
    value2 = secrets.get_required_secret("database/url")
    assert value2 == "old_value"  # Still cached

    # After refresh, new value returned
    secrets.refresh_secrets()
    value3 = secrets.get_required_secret("database/url")
    assert value3 == "new_value"  # Fresh from backend


def test_get_secret_uncached_for_cert_rotation(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """AC7d Test 28: get_secret_uncached() bypasses cache for cert rotation."""
    monkeypatch.setenv("SECRETS_VALIDATION_MODE", "strict")
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")
    monkeypatch.setenv("KILLSWITCH_MTLS_CERT_PATH", "/old/cert.pem")

    # First read
    path1 = secrets.get_secret_uncached("killswitch/mtls_cert_path")
    assert path1 == "/old/cert.pem"

    # Update path (simulating cert rotation)
    monkeypatch.setenv("KILLSWITCH_MTLS_CERT_PATH", "/new/cert.pem")

    # Uncached read picks up new value immediately (no refresh needed)
    path2 = secrets.get_secret_uncached("killswitch/mtls_cert_path")
    assert path2 == "/new/cert.pem"  # No restart needed


def test_invalidate_secret_clears_cache(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """Rotation: invalidate_secret() clears cache for subsequent reads."""
    monkeypatch.setenv("SECRETS_VALIDATION_MODE", "strict")
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", "old_value")

    # First read caches
    value1 = secrets.get_required_secret("database/url")
    assert value1 == "old_value"

    # Update env var
    monkeypatch.setenv("DATABASE_URL", "new_value")

    # Invalidate cache
    secrets.invalidate_secret("database/url")

    # Next read fetches fresh value
    value2 = secrets.get_required_secret("database/url")
    assert value2 == "new_value"


def test_invalidate_secret_handles_missing_cache_gracefully(
    monkeypatch: pytest.MonkeyPatch, clean_env: None, caplog: pytest.LogCaptureFixture
) -> None:
    """invalidate_secret() handles backends without _cache attribute."""
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")

    # Create mock manager without _cache attribute
    mock_mgr = MagicMock(spec=[])  # No _cache attribute
    secrets._secret_manager = mock_mgr

    # Should not raise, just log warning
    secrets.invalidate_secret("database/url")
    assert "invalidate_secret_no_cache" in caplog.text


def test_invalidate_secret_clears_both_formats(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """invalidate_secret() clears both hierarchical and env var format."""
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", "test_value")

    mgr = secrets.get_secret_manager()

    # Read to populate cache
    mgr.get_secret("database/url")

    # Verify cache has entry (both formats may be present)
    assert hasattr(mgr, "_cache")

    # Invalidate
    secrets.invalidate_secret("database/url")

    # Both hierarchical and env var formats should be cleared
    # Note: EnvSecretManager uses env var format for cache keys
    assert mgr._cache._cache.get("database/url") is None
    assert mgr._cache._cache.get("DATABASE_URL") is None


# ============================================================================
# Optional Secret Getters
# ============================================================================


def test_get_optional_secret_returns_default_when_missing(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """get_optional_secret() returns default for missing secrets."""
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")

    result = secrets.get_optional_secret("redis/password", "default_value")
    assert result == "default_value"


def test_get_optional_secret_returns_value_when_present(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """get_optional_secret() returns value when present."""
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")
    monkeypatch.setenv("REDIS_PASSWORD", "actual_password")

    result = secrets.get_optional_secret("redis/password", "default_value")
    assert result == "actual_password"


def test_get_optional_secret_returns_default_for_empty(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """get_optional_secret() returns default for empty values."""
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")
    monkeypatch.setenv("REDIS_PASSWORD", "")

    result = secrets.get_optional_secret("redis/password", "default_value")
    assert result == "default_value"


def test_get_optional_secret_or_none_returns_none_when_missing(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """get_optional_secret_or_none() returns None for missing secrets."""
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")

    result = secrets.get_optional_secret_or_none("redis/password")
    assert result is None


def test_get_optional_secret_or_none_returns_value_when_present(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """get_optional_secret_or_none() returns value when present."""
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")
    monkeypatch.setenv("REDIS_PASSWORD", "actual_password")

    result = secrets.get_optional_secret_or_none("redis/password")
    assert result == "actual_password"


def test_get_optional_secret_or_none_returns_none_for_empty(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """get_optional_secret_or_none() returns None for empty values."""
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")
    monkeypatch.setenv("REDIS_PASSWORD", "")

    result = secrets.get_optional_secret_or_none("redis/password")
    assert result is None


# ============================================================================
# Path Secret Getter
# ============================================================================


def test_get_path_secret_returns_value_when_present(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """get_path_secret() returns path when present."""
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")
    monkeypatch.setenv("SECRETS_VALIDATION_MODE", "strict")
    monkeypatch.setenv("KILLSWITCH_MTLS_CERT_PATH", "/etc/certs/cert.pem")

    result = secrets.get_path_secret("killswitch/mtls_cert_path")
    assert result == "/etc/certs/cert.pem"


def test_get_path_secret_fails_when_required_missing(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """get_path_secret() fails for required path when missing."""
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")
    monkeypatch.setenv("SECRETS_VALIDATION_MODE", "strict")

    with pytest.raises(RuntimeError, match="Required secret 'killswitch/mtls_cert_path'"):
        secrets.get_path_secret("killswitch/mtls_cert_path")


def test_get_path_secret_returns_default_when_optional_missing(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """get_path_secret() returns default for optional path when missing."""
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")

    result = secrets.get_path_secret("some/optional/path", "/default/path")
    assert result == "/default/path"


# ============================================================================
# AC7e: Factory Guardrails
# ============================================================================


def test_factory_guardrail_env_backend_non_local_fails(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """AC7e Test 7: Factory guardrail prevents env backend in non-local."""
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "production")

    # Factory should raise SecretManagerError on create_secret_manager()
    from libs.secrets.exceptions import SecretManagerError

    with pytest.raises(SecretManagerError, match="EnvSecretManager not allowed in production"):
        secrets.get_secret_manager()


def test_factory_allows_env_backend_in_local(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """Factory allows env backend in local environment."""
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", "test_url")

    # Should not raise
    mgr = secrets.get_secret_manager()
    assert mgr is not None


def test_factory_allows_env_backend_with_override(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """Factory allows env backend with SECRET_ALLOW_ENV_IN_NON_LOCAL override."""
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "staging")  # Non-local environment
    monkeypatch.setenv("SECRET_ALLOW_ENV_IN_NON_LOCAL", "1")  # Override flag
    monkeypatch.setenv("DATABASE_URL", "test_url")

    # Should not raise with override enabled
    mgr = secrets.get_secret_manager()
    assert mgr is not None


# ============================================================================
# AC7e: Conditional Requirements (DRY_RUN, ENVIRONMENT)
# ============================================================================


def test_conditional_requirements_dry_run_false_requires_alpaca(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """AC7e Test 8: DRY_RUN=false requires Alpaca keys."""
    monkeypatch.setenv("SECRETS_VALIDATION_MODE", "strict")
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", "test_url")
    monkeypatch.setenv("DRY_RUN", "false")  # Explicitly set to false
    # Alpaca keys not set

    # Simulating execution_gateway startup logic
    required = ["database/url"]
    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
    if not dry_run:
        required.extend(["alpaca/api_key_id", "alpaca/api_secret_key"])

    with pytest.raises(RuntimeError, match="Missing required secrets"):
        secrets.validate_required_secrets(required)


def test_conditional_requirements_dry_run_true_skips_alpaca(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """AC7e Test 8b: DRY_RUN=true allows missing Alpaca keys."""
    monkeypatch.setenv("SECRETS_VALIDATION_MODE", "strict")
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", "test_url")
    monkeypatch.setenv("DRY_RUN", "true")

    # Simulating execution_gateway startup logic
    required = ["database/url"]
    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
    if not dry_run:
        required.extend(["alpaca/api_key_id", "alpaca/api_secret_key"])

    # Should not raise - Alpaca keys not required in dry run
    secrets.validate_required_secrets(required)


def test_conditional_requirements_prod_requires_webhook_secret(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """AC7e Test 8c: Production environment requires webhook secret."""
    monkeypatch.setenv("SECRETS_VALIDATION_MODE", "strict")
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", "test_url")
    monkeypatch.setenv("ENVIRONMENT", "prod")

    # Simulating execution_gateway startup logic
    required = ["database/url"]
    environment = os.getenv("ENVIRONMENT", "dev")
    if environment not in ("dev", "test"):
        required.append("webhook/secret")

    with pytest.raises(RuntimeError, match="Missing required secrets"):
        secrets.validate_required_secrets(required)


def test_conditional_requirements_dev_allows_missing_webhook(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """AC7e Test 8d: Dev environment allows missing webhook secret."""
    monkeypatch.setenv("SECRETS_VALIDATION_MODE", "strict")
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", "test_url")
    monkeypatch.setenv("ENVIRONMENT", "dev")

    # Simulating execution_gateway startup logic
    required = ["database/url"]
    environment = os.getenv("ENVIRONMENT", "dev")
    if environment not in ("dev", "test"):
        required.append("webhook/secret")

    # Should not raise - webhook secret not required in dev
    secrets.validate_required_secrets(required)


# ============================================================================
# AC7e: Dry-Run Startup Test
# ============================================================================


def test_dry_run_startup_successful(monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    """AC7e Test 10: Dry-run mode allows startup with minimal secrets."""
    monkeypatch.setenv("SECRETS_VALIDATION_MODE", "strict")
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("DATABASE_URL", "postgresql://test:test@localhost/test")

    # Simulating execution_gateway startup in dry-run mode
    required = ["database/url"]
    dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
    environment = os.getenv("ENVIRONMENT", "dev")

    if not dry_run:
        required.extend(["alpaca/api_key_id", "alpaca/api_secret_key"])
    if environment not in ("dev", "test"):
        required.append("webhook/secret")

    # Should not raise - only DATABASE_URL required in dry-run dev mode
    secrets.validate_required_secrets(required)


# ============================================================================
# Lifecycle Management
# ============================================================================


def test_singleton_teardown_closes_manager(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """Singleton teardown properly closes manager."""
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", "test_url")

    # Create manager
    mgr = secrets.get_secret_manager()
    assert mgr is not None
    assert secrets._secret_manager is mgr

    # Close
    secrets.close_secret_manager()
    assert secrets._secret_manager is None


def test_singleton_returns_same_instance(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """get_secret_manager() returns same instance on repeated calls."""
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", "test_url")

    mgr1 = secrets.get_secret_manager()
    mgr2 = secrets.get_secret_manager()
    assert mgr1 is mgr2


# ============================================================================
# Import-Time Safety (Prevent Regressions)
# ============================================================================


def test_no_secrets_read_at_import(monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    """AC7e Test 24: Importing libs.common.secrets doesn't read secrets.

    This prevents import-time secret access (regressions).
    """
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")
    # DATABASE_URL not set

    # Import should not raise (no secret access at import time)
    import importlib

    import libs.common.secrets as secrets_module

    importlib.reload(secrets_module)

    # Manager should still be None (not created until first get_secret_manager() call)
    assert secrets_module._secret_manager is None


def test_value_whitespace_stripping(monkeypatch: pytest.MonkeyPatch, clean_env: None) -> None:
    """Secret values are stripped of leading/trailing whitespace."""
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")
    monkeypatch.setenv("SECRETS_VALIDATION_MODE", "strict")
    monkeypatch.setenv("DATABASE_URL", "  postgresql://test  \n")

    result = secrets.get_required_secret("database/url")
    assert result == "postgresql://test"  # Stripped


# ============================================================================
# Backend Error Handling
# ============================================================================


def test_backend_access_error_strict_mode(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """SecretAccessError fails in strict mode."""
    monkeypatch.setenv("SECRETS_VALIDATION_MODE", "strict")
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")

    # Mock backend raising SecretAccessError (requires secret_name, backend, reason)
    mock_mgr = MagicMock()
    mock_mgr.get_secret.side_effect = SecretAccessError(
        secret_name="database/url", backend="env", reason="Backend error"
    )
    secrets._secret_manager = mock_mgr

    with pytest.raises(RuntimeError, match="Failed to access secret 'database/url'"):
        secrets.get_required_secret("database/url")


def test_backend_access_error_warn_mode_non_critical(
    monkeypatch: pytest.MonkeyPatch, clean_env: None, caplog: pytest.LogCaptureFixture
) -> None:
    """SecretAccessError returns empty in warn mode for non-critical."""
    monkeypatch.setenv("SECRETS_VALIDATION_MODE", "warn")
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")

    # Mock backend raising SecretAccessError for non-critical secret
    mock_mgr = MagicMock()
    mock_mgr.get_secret.side_effect = SecretAccessError(
        secret_name="redis/password", backend="env", reason="Backend error"
    )
    secrets._secret_manager = mock_mgr

    result = secrets.get_required_secret("redis/password")
    assert result == ""
    assert "secrets_access_error_warn" in caplog.text


def test_backend_access_error_warn_mode_critical_fails(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """SecretAccessError fails for critical secrets even in warn mode."""
    monkeypatch.setenv("SECRETS_VALIDATION_MODE", "warn")
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")

    # Mock backend raising SecretAccessError for critical secret
    mock_mgr = MagicMock()
    mock_mgr.get_secret.side_effect = SecretAccessError(
        secret_name="database/url", backend="env", reason="Backend error"
    )
    secrets._secret_manager = mock_mgr

    with pytest.raises(RuntimeError, match="Failed to access secret 'database/url'"):
        secrets.get_required_secret("database/url")


# ============================================================================
# Edge Cases
# ============================================================================


def test_env_var_name_conversion() -> None:
    """_to_env_var_name converts hierarchical to env var format."""
    assert secrets._to_env_var_name("database/url") == "DATABASE_URL"
    assert secrets._to_env_var_name("alpaca/api_key_id") == "ALPACA_API_KEY_ID"
    assert secrets._to_env_var_name("killswitch/mtls_cert_path") == "KILLSWITCH_MTLS_CERT_PATH"


def test_critical_secrets_constant_completeness() -> None:
    """CRITICAL_SECRETS constant includes all safety-critical secrets."""
    expected_critical = {
        "database/url",
        "alpaca/api_key_id",
        "alpaca/api_secret_key",
        "killswitch/mtls_cert_path",
        "killswitch/mtls_key_path",
        "killswitch/ca_cert_path",
        "killswitch/jwt_signing_key",
        "killswitch/jwt_verification_key",
    }
    assert secrets.CRITICAL_SECRETS == expected_critical


def test_validate_required_secrets_partial_success(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """validate_required_secrets reports all missing secrets in one error."""
    monkeypatch.setenv("SECRETS_VALIDATION_MODE", "strict")
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")
    monkeypatch.setenv("DATABASE_URL", "valid_url")
    # alpaca/api_key_id missing

    with pytest.raises(RuntimeError) as exc_info:
        secrets.validate_required_secrets(["database/url", "alpaca/api_key_id"])

    error_msg = str(exc_info.value)
    assert "alpaca/api_key_id" in error_msg
    assert "database/url" not in error_msg  # DATABASE_URL was valid


def test_get_optional_secret_handles_backend_error_gracefully(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """get_optional_secret returns default on backend errors."""
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")

    mock_mgr = MagicMock()
    mock_mgr.get_secret.side_effect = SecretAccessError(
        secret_name="redis/password", backend="env", reason="Backend error"
    )
    secrets._secret_manager = mock_mgr

    result = secrets.get_optional_secret("redis/password", "default_val")
    assert result == "default_val"


def test_get_optional_secret_or_none_handles_backend_error_gracefully(
    monkeypatch: pytest.MonkeyPatch, clean_env: None
) -> None:
    """get_optional_secret_or_none returns None on backend errors."""
    monkeypatch.setenv("SECRET_BACKEND", "env")
    monkeypatch.setenv("DEPLOYMENT_ENV", "local")

    mock_mgr = MagicMock()
    mock_mgr.get_secret.side_effect = SecretAccessError(
        secret_name="redis/password", backend="env", reason="Backend error"
    )
    secrets._secret_manager = mock_mgr

    result = secrets.get_optional_secret_or_none("redis/password")
    assert result is None
