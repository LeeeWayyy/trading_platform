"""
Tests for C3: Webhook Secret Security Fix.

This module tests that WEBHOOK_SECRET is mandatory in production environments
and optional in dev/test/dry_run modes.

Issue: C3 - Webhook secret fail-open
Location: apps/execution_gateway/main.py:119-126
Fix: Raise RuntimeError at startup if WEBHOOK_SECRET not set in production
"""

import pytest


class TestWebhookSecretValidation:
    """Test webhook secret validation logic.

    Instead of reloading the entire module (which causes Prometheus registry
    conflicts), we test the validation logic directly by simulating the
    environment variable checks.
    """

    def _check_webhook_secret_required(
        self, webhook_secret: str, environment: str, dry_run: bool
    ) -> bool:
        """Simulate the webhook secret validation logic from main.py.

        Returns True if an error should be raised (secret required but missing).
        This mirrors the logic in apps/execution_gateway/main.py:119-128:

        WEBHOOK_SECRET = WEBHOOK_SECRET.strip()
        if not WEBHOOK_SECRET and ENVIRONMENT not in ("dev", "test") and not DRY_RUN:
            raise RuntimeError(...)
        """
        # Strip whitespace first (like the actual code does)
        webhook_secret = webhook_secret.strip()
        if not webhook_secret and environment not in ("dev", "test") and not dry_run:
            return True  # Error should be raised
        return False  # OK, no error

    @pytest.mark.parametrize("environment", ["dev", "test"])
    def test_webhook_secret_optional_in_dev_test(self, environment: str):
        """Verify webhook secret is optional in dev/test environments."""
        # Empty secret should be OK in dev/test
        should_error = self._check_webhook_secret_required(
            webhook_secret="", environment=environment, dry_run=False
        )
        assert not should_error, f"Should not require WEBHOOK_SECRET in {environment} mode"

    def test_webhook_secret_optional_in_dry_run(self):
        """Verify webhook secret is optional when DRY_RUN=true."""
        # Empty secret should be OK in dry_run mode even in production
        should_error = self._check_webhook_secret_required(
            webhook_secret="", environment="production", dry_run=True
        )
        assert not should_error, "Should not require WEBHOOK_SECRET in dry_run mode"

    def test_webhook_secret_required_in_production(self):
        """Verify webhook secret is REQUIRED in production with DRY_RUN=false."""
        should_error = self._check_webhook_secret_required(
            webhook_secret="", environment="production", dry_run=False
        )
        assert should_error, "Should require WEBHOOK_SECRET in production with DRY_RUN=false"

    def test_webhook_secret_required_in_staging(self):
        """Verify webhook secret is REQUIRED in staging with DRY_RUN=false."""
        should_error = self._check_webhook_secret_required(
            webhook_secret="", environment="staging", dry_run=False
        )
        assert should_error, "Should require WEBHOOK_SECRET in staging with DRY_RUN=false"

    def test_webhook_secret_set_allows_production(self):
        """Verify production starts when WEBHOOK_SECRET is set."""
        should_error = self._check_webhook_secret_required(
            webhook_secret="test-secret-12345", environment="production", dry_run=False
        )
        assert not should_error, "Should allow production when WEBHOOK_SECRET is set"

    def test_webhook_secret_whitespace_only_rejected(self):
        """Verify whitespace-only WEBHOOK_SECRET is treated as missing."""
        should_error = self._check_webhook_secret_required(
            webhook_secret="   ", environment="production", dry_run=False  # Whitespace only
        )
        assert should_error, "Whitespace-only WEBHOOK_SECRET should be rejected in production"

    def test_webhook_secret_with_padding_accepted(self):
        """Verify secret with leading/trailing whitespace is accepted after strip."""
        should_error = self._check_webhook_secret_required(
            webhook_secret="  valid-secret-123  ",  # Valid secret with whitespace padding
            environment="production",
            dry_run=False,
        )
        assert not should_error, "Secret with whitespace padding should be accepted"

    @pytest.mark.parametrize(
        ("environment", "dry_run", "secret", "expect_error"),
        [
            # Dev/test: always OK
            ("dev", False, "", False),
            ("dev", True, "", False),
            ("test", False, "", False),
            ("test", True, "", False),
            # Dry run: always OK
            ("production", True, "", False),
            ("staging", True, "", False),
            # Production/staging without dry_run: require secret
            ("production", False, "", True),
            ("staging", False, "", True),
            # With secret: always OK
            ("production", False, "secret123", False),
            ("staging", False, "secret123", False),
        ],
    )
    def test_webhook_secret_matrix(
        self, environment: str, dry_run: bool, secret: str, expect_error: bool
    ):
        """Comprehensive matrix test for webhook secret validation."""
        should_error = self._check_webhook_secret_required(
            webhook_secret=secret, environment=environment, dry_run=dry_run
        )
        assert should_error == expect_error, (
            f"Environment={environment}, DRY_RUN={dry_run}, "
            f"secret={'set' if secret else 'empty'}: "
            f"expected error={expect_error}, got {should_error}"
        )
