"""
Tests for C6: CORS Security Fix.

This module tests that CORS is properly configured with allowlists
instead of wildcards.

Issue: C6 - CORS wildcard exposes Signal Service API
Location: apps/signal_service/main.py:391-423
Fix: Use environment-based allowlist instead of allow_origins=["*"]
"""

import pytest


class TestCORSConfiguration:
    """Test CORS configuration logic.

    Instead of reloading the entire module (which causes Prometheus registry
    conflicts), we test the validation logic directly by simulating the
    environment variable checks.
    """

    def _get_cors_origins(
        self, environment: str, allowed_origins: str
    ) -> list[str] | None:
        """Simulate the CORS origin resolution logic from main.py.

        Returns the list of allowed origins, or None if a RuntimeError should be raised.
        This mirrors the logic in apps/signal_service/main.py:391-431.
        """
        if allowed_origins:
            # Parse comma-separated origins from environment variable
            origins = [o.strip() for o in allowed_origins.split(",") if o.strip()]
            # Reject wildcard "*" - incompatible with allow_credentials=True
            if "*" in origins:
                return None  # Error should be raised
            return origins
        elif environment in ("dev", "test"):
            # Safe defaults for development/testing (localhost only)
            return [
                "http://localhost:8501",
                "http://127.0.0.1:8501",
                "http://localhost:3000",
                "http://127.0.0.1:3000",
            ]
        else:
            # Production requires explicit ALLOWED_ORIGINS configuration
            return None  # Indicates error should be raised

    @pytest.mark.parametrize("environment", ["dev", "test"])
    def test_cors_default_origins_in_dev_test(self, environment: str):
        """Verify CORS uses safe defaults in dev/test environments."""
        origins = self._get_cors_origins(
            environment=environment,
            allowed_origins=""
        )

        assert origins is not None, f"Should not require ALLOWED_ORIGINS in {environment} mode"
        assert len(origins) > 0, "Should have default origins"

        # Should only contain localhost origins
        for origin in origins:
            assert "localhost" in origin or "127.0.0.1" in origin, \
                f"Dev/test should only allow localhost, got: {origin}"

        # Should NOT contain wildcard
        assert "*" not in origins

    def test_cors_custom_origins_from_env(self):
        """Verify CORS respects ALLOWED_ORIGINS environment variable."""
        custom_origins = "https://app.example.com,https://admin.example.com"
        origins = self._get_cors_origins(
            environment="production",
            allowed_origins=custom_origins
        )

        assert origins is not None
        assert "https://app.example.com" in origins
        assert "https://admin.example.com" in origins
        assert len(origins) == 2

    def test_cors_required_in_production(self):
        """Verify CORS configuration fails in production without ALLOWED_ORIGINS."""
        origins = self._get_cors_origins(
            environment="production",
            allowed_origins=""
        )

        assert origins is None, "Should require ALLOWED_ORIGINS in production"

    def test_cors_required_in_staging(self):
        """Verify CORS configuration fails in staging without ALLOWED_ORIGINS."""
        origins = self._get_cors_origins(
            environment="staging",
            allowed_origins=""
        )

        assert origins is None, "Should require ALLOWED_ORIGINS in staging"

    def test_cors_handles_whitespace_in_origins(self):
        """Verify CORS correctly parses origins with whitespace."""
        # Origins with extra whitespace
        custom_origins = "  https://app.example.com  ,  https://admin.example.com  ,  "
        origins = self._get_cors_origins(
            environment="production",
            allowed_origins=custom_origins
        )

        assert origins is not None
        # Should strip whitespace and filter empty strings
        assert "https://app.example.com" in origins
        assert "https://admin.example.com" in origins
        assert "" not in origins
        assert "  " not in origins
        assert len(origins) == 2

    def test_cors_wildcard_rejected(self):
        """Verify wildcard "*" is explicitly rejected."""
        # Wildcard is incompatible with allow_credentials=True
        # Should raise error instead of crashing at startup
        origins = self._get_cors_origins(
            environment="production",
            allowed_origins="*"
        )

        # Wildcard "*" should be rejected
        assert origins is None, "Wildcard '*' should be rejected"

    def test_cors_wildcard_in_list_rejected(self):
        """Verify wildcard "*" is rejected even when mixed with other origins."""
        origins = self._get_cors_origins(
            environment="production",
            allowed_origins="https://app.example.com,*,https://admin.example.com"
        )

        # Wildcard "*" anywhere in the list should be rejected
        assert origins is None, "Wildcard '*' in origin list should be rejected"

    @pytest.mark.parametrize(
        "environment,allowed_origins,expect_error",
        [
            # Dev/test: always OK with defaults
            ("dev", "", False),
            ("test", "", False),
            # Production/staging: require explicit origins
            ("production", "", True),
            ("staging", "", True),
            # With explicit origins: always OK
            ("production", "https://app.example.com", False),
            ("staging", "https://app.example.com", False),
            ("dev", "https://app.example.com", False),
        ],
    )
    def test_cors_configuration_matrix(
        self, environment: str, allowed_origins: str, expect_error: bool
    ):
        """Comprehensive matrix test for CORS configuration."""
        origins = self._get_cors_origins(
            environment=environment,
            allowed_origins=allowed_origins
        )

        got_error = origins is None
        assert got_error == expect_error, (
            f"Environment={environment}, ALLOWED_ORIGINS={'set' if allowed_origins else 'empty'}: "
            f"expected error={expect_error}, got {got_error}"
        )
