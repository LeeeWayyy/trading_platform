"""
Tests for M6: TRUSTED_PROXY_IPS Dev Defaults (web_console).

M6 Fix: Ensures dev/test environments get safe localhost defaults.
Env var always overrides defaults when explicitly set.
Uses shared get_trusted_proxy_ips() from libs/common/network_utils.
"""

import os
from unittest.mock import patch

import pytest

from libs.common.network_utils import get_trusted_proxy_ips


class TestTrustedProxyIpsWebConsole:
    """Test suite for M6 TRUSTED_PROXY_IPS using shared network_utils."""

    def test_proxy_ips_default_dev(self) -> None:
        """ENVIRONMENT=dev should use localhost defaults."""
        with patch.dict(os.environ, {"ENVIRONMENT": "dev"}, clear=True):
            os.environ.pop("TRUSTED_PROXY_IPS", None)
            result = get_trusted_proxy_ips()

        assert result == ["127.0.0.1", "::1"]

    def test_proxy_ips_default_test(self) -> None:
        """ENVIRONMENT=test should use localhost defaults."""
        with patch.dict(os.environ, {"ENVIRONMENT": "test"}, clear=True):
            os.environ.pop("TRUSTED_PROXY_IPS", None)
            result = get_trusted_proxy_ips()

        assert result == ["127.0.0.1", "::1"]

    def test_proxy_ips_env_override(self) -> None:
        """Explicit TRUSTED_PROXY_IPS should override defaults."""
        with patch.dict(
            os.environ,
            {"ENVIRONMENT": "dev", "TRUSTED_PROXY_IPS": "10.0.0.1"},
            clear=True,
        ):
            result = get_trusted_proxy_ips()

        assert result == ["10.0.0.1"]

    def test_proxy_ips_prod_fails_closed(self) -> None:
        """ENVIRONMENT=prod without TRUSTED_PROXY_IPS should raise RuntimeError."""
        with patch.dict(os.environ, {"ENVIRONMENT": "prod"}, clear=True):
            os.environ.pop("TRUSTED_PROXY_IPS", None)

            with pytest.raises(RuntimeError, match="must be configured"):
                get_trusted_proxy_ips()

    def test_proxy_ips_prod_with_explicit_config(self) -> None:
        """ENVIRONMENT=prod with TRUSTED_PROXY_IPS should succeed."""
        with patch.dict(
            os.environ,
            {"ENVIRONMENT": "prod", "TRUSTED_PROXY_IPS": "10.0.0.1,10.0.0.2"},
            clear=True,
        ):
            result = get_trusted_proxy_ips()

        assert result == ["10.0.0.1", "10.0.0.2"]
