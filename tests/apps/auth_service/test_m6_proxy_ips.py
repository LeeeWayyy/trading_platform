"""
Tests for M6: TRUSTED_PROXY_IPS Dev Defaults (auth_service).

M6 Fix: Ensures dev/test environments get safe localhost defaults
while prod/staging require explicit configuration (fail-closed).
"""

import os
from unittest.mock import patch

import pytest

# Import after patching to test module initialization
# We'll reload the module after patching environment


class TestTrustedProxyIpsAuthService:
    """Test suite for M6 TRUSTED_PROXY_IPS in auth_service."""

    def test_proxy_ips_default_dev(self) -> None:
        """ENVIRONMENT=dev should use localhost defaults."""
        with patch.dict(os.environ, {"ENVIRONMENT": "dev"}, clear=True):
            # Remove TRUSTED_PROXY_IPS to test defaults
            os.environ.pop("TRUSTED_PROXY_IPS", None)

            from apps.auth_service.main import _validate_trusted_proxy_ips

            result = _validate_trusted_proxy_ips()

        assert result == ["127.0.0.1", "::1"]

    def test_proxy_ips_default_test(self) -> None:
        """ENVIRONMENT=test should use localhost defaults."""
        with patch.dict(os.environ, {"ENVIRONMENT": "test"}, clear=True):
            os.environ.pop("TRUSTED_PROXY_IPS", None)

            from apps.auth_service.main import _validate_trusted_proxy_ips

            result = _validate_trusted_proxy_ips()

        assert result == ["127.0.0.1", "::1"]

    def test_proxy_ips_default_development(self) -> None:
        """ENVIRONMENT=development should use localhost defaults."""
        with patch.dict(os.environ, {"ENVIRONMENT": "development"}, clear=True):
            os.environ.pop("TRUSTED_PROXY_IPS", None)

            from apps.auth_service.main import _validate_trusted_proxy_ips

            result = _validate_trusted_proxy_ips()

        assert result == ["127.0.0.1", "::1"]

    def test_proxy_ips_env_override_dev(self) -> None:
        """Explicit TRUSTED_PROXY_IPS should override defaults even in dev."""
        with patch.dict(
            os.environ,
            {"ENVIRONMENT": "dev", "TRUSTED_PROXY_IPS": "10.0.0.1,10.0.0.2"},
            clear=True,
        ):
            from apps.auth_service.main import _validate_trusted_proxy_ips

            result = _validate_trusted_proxy_ips()

        assert result == ["10.0.0.1", "10.0.0.2"]

    def test_proxy_ips_empty_override_dev(self) -> None:
        """Explicit empty TRUSTED_PROXY_IPS should override defaults (empty list)."""
        with patch.dict(
            os.environ,
            {"ENVIRONMENT": "dev", "TRUSTED_PROXY_IPS": ""},
            clear=True,
        ):
            from apps.auth_service.main import _validate_trusted_proxy_ips

            result = _validate_trusted_proxy_ips()

        assert result == []

    def test_proxy_ips_prod_requires_config(self) -> None:
        """ENVIRONMENT=prod without TRUSTED_PROXY_IPS should raise RuntimeError."""
        with patch.dict(os.environ, {"ENVIRONMENT": "prod"}, clear=True):
            os.environ.pop("TRUSTED_PROXY_IPS", None)

            from apps.auth_service.main import _validate_trusted_proxy_ips

            with pytest.raises(RuntimeError, match="must be configured"):
                _validate_trusted_proxy_ips()

    def test_proxy_ips_staging_requires_config(self) -> None:
        """ENVIRONMENT=staging without TRUSTED_PROXY_IPS should raise RuntimeError."""
        with patch.dict(os.environ, {"ENVIRONMENT": "staging"}, clear=True):
            os.environ.pop("TRUSTED_PROXY_IPS", None)

            from apps.auth_service.main import _validate_trusted_proxy_ips

            with pytest.raises(RuntimeError, match="must be configured"):
                _validate_trusted_proxy_ips()

    def test_proxy_ips_prod_with_config(self) -> None:
        """ENVIRONMENT=prod with TRUSTED_PROXY_IPS should work."""
        with patch.dict(
            os.environ,
            {"ENVIRONMENT": "prod", "TRUSTED_PROXY_IPS": "10.0.0.1"},
            clear=True,
        ):
            from apps.auth_service.main import _validate_trusted_proxy_ips

            result = _validate_trusted_proxy_ips()

        assert result == ["10.0.0.1"]

    def test_proxy_ips_case_insensitive(self) -> None:
        """ENVIRONMENT check should be case-insensitive."""
        with patch.dict(os.environ, {"ENVIRONMENT": "DEV"}, clear=True):
            os.environ.pop("TRUSTED_PROXY_IPS", None)

            from apps.auth_service.main import _validate_trusted_proxy_ips

            result = _validate_trusted_proxy_ips()

        assert result == ["127.0.0.1", "::1"]
