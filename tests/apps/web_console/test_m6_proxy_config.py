"""
Tests for M6: TRUSTED_PROXY_IPS Dev Defaults (web_console).

M6 Fix: Ensures dev/test environments get safe localhost defaults.
Env var always overrides defaults when explicitly set.
"""

import importlib
import os
from unittest.mock import patch


class TestTrustedProxyIpsWebConsole:
    """Test suite for M6 TRUSTED_PROXY_IPS in web_console/config.py."""

    def test_proxy_ips_default_dev(self) -> None:
        """ENVIRONMENT=dev should use localhost defaults."""
        with patch.dict(os.environ, {"ENVIRONMENT": "dev"}, clear=True):
            os.environ.pop("TRUSTED_PROXY_IPS", None)

            # Reimport to pick up new env
            from apps.web_console.config import _get_trusted_proxy_ips

            result = _get_trusted_proxy_ips()

        assert result == ["127.0.0.1", "::1"]

    def test_proxy_ips_default_test(self) -> None:
        """ENVIRONMENT=test should use localhost defaults."""
        with patch.dict(os.environ, {"ENVIRONMENT": "test"}, clear=True):
            os.environ.pop("TRUSTED_PROXY_IPS", None)

            from apps.web_console.config import _get_trusted_proxy_ips

            result = _get_trusted_proxy_ips()

        assert result == ["127.0.0.1", "::1"]

    def test_proxy_ips_env_override(self) -> None:
        """Explicit TRUSTED_PROXY_IPS should override defaults."""
        with patch.dict(
            os.environ,
            {"ENVIRONMENT": "dev", "TRUSTED_PROXY_IPS": "10.0.0.1"},
            clear=True,
        ):
            from apps.web_console.config import _get_trusted_proxy_ips

            result = _get_trusted_proxy_ips()

        assert result == ["10.0.0.1"]

    def test_proxy_ips_prod_no_defaults(self) -> None:
        """ENVIRONMENT=prod without TRUSTED_PROXY_IPS should return empty list."""
        with patch.dict(os.environ, {"ENVIRONMENT": "prod"}, clear=True):
            os.environ.pop("TRUSTED_PROXY_IPS", None)

            from apps.web_console.config import _get_trusted_proxy_ips

            result = _get_trusted_proxy_ips()

        # web_console doesn't raise, just returns empty
        assert result == []
