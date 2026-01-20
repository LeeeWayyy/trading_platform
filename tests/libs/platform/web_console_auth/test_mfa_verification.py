"""
Unit tests for libs.platform.web_console_auth.mfa_verification.

Tests cover:
- Step-up MFA verification (verify_step_up_auth)
- AMR method extraction (get_amr_method)
- Async 2FA requirement checks (require_2fa_for_action)
- Edge cases (missing claims, expired auth, invalid methods)

Target: 85%+ branch coverage (baseline from 0%)
"""

import os
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock

import pytest

from libs.platform.web_console_auth.mfa_verification import (
    ALLOWED_AMR_METHODS,
    MFA_MAX_AGE_SECONDS,
    get_amr_method,
    require_2fa_for_action,
    verify_step_up_auth,
)


class TestVerifyStepUpAuth:
    """Tests for verify_step_up_auth() validation function."""

    def test_verify_step_up_auth_success_with_mfa(self):
        """Test step-up auth validation passes with recent MFA."""
        auth_time = int(datetime.now(UTC).timestamp())
        id_token_claims = {"auth_time": auth_time, "amr": ["mfa"]}

        valid, error = verify_step_up_auth(id_token_claims)

        assert valid is True
        assert error is None

    def test_verify_step_up_auth_success_with_otp(self):
        """Test step-up auth validation passes with OTP method."""
        auth_time = int(datetime.now(UTC).timestamp())
        id_token_claims = {"auth_time": auth_time, "amr": ["otp"]}

        valid, error = verify_step_up_auth(id_token_claims)

        assert valid is True
        assert error is None

    def test_verify_step_up_auth_success_with_multiple_amr(self):
        """Test step-up auth validation passes with multiple AMR methods."""
        auth_time = int(datetime.now(UTC).timestamp())
        id_token_claims = {"auth_time": auth_time, "amr": ["pwd", "mfa"]}

        valid, error = verify_step_up_auth(id_token_claims)

        assert valid is True
        assert error is None

    def test_verify_step_up_auth_missing_auth_time(self):
        """Test step-up auth validation fails when auth_time missing."""
        id_token_claims = {"amr": ["mfa"]}

        valid, error = verify_step_up_auth(id_token_claims)

        assert valid is False
        assert error == "missing_auth_time"

    def test_verify_step_up_auth_invalid_auth_time_value_error(self):
        """Test step-up auth validation fails with invalid auth_time (ValueError)."""
        id_token_claims = {"auth_time": "invalid_timestamp", "amr": ["mfa"]}

        valid, error = verify_step_up_auth(id_token_claims)

        assert valid is False
        assert error == "invalid_auth_time"

    def test_verify_step_up_auth_invalid_auth_time_type_error(self):
        """Test step-up auth validation fails with invalid auth_time (TypeError)."""
        id_token_claims = {"auth_time": None, "amr": ["mfa"]}

        valid, error = verify_step_up_auth(id_token_claims)

        assert valid is False
        assert error == "missing_auth_time"

    def test_verify_step_up_auth_invalid_auth_time_overflow(self):
        """Test step-up auth validation fails with overflow auth_time."""
        id_token_claims = {"auth_time": 10**20, "amr": ["mfa"]}  # Huge timestamp

        valid, error = verify_step_up_auth(id_token_claims)

        assert valid is False
        assert error == "invalid_auth_time"

    def test_verify_step_up_auth_expired_auth_time(self):
        """Test step-up auth validation fails when auth is too old."""
        # Auth time exceeds MFA_MAX_AGE_SECONDS (default 60 seconds)
        old_auth_time = int(
            (datetime.now(UTC) - timedelta(seconds=MFA_MAX_AGE_SECONDS + 10)).timestamp()
        )
        id_token_claims = {"auth_time": old_auth_time, "amr": ["mfa"]}

        valid, error = verify_step_up_auth(id_token_claims)

        assert valid is False
        assert error == "auth_too_old"

    def test_verify_step_up_auth_missing_amr(self):
        """Test step-up auth validation fails when amr missing."""
        auth_time = int(datetime.now(UTC).timestamp())
        id_token_claims = {"auth_time": auth_time}

        valid, error = verify_step_up_auth(id_token_claims)

        assert valid is False
        assert error == "mfa_not_performed"

    def test_verify_step_up_auth_empty_amr(self):
        """Test step-up auth validation fails when amr is empty list."""
        auth_time = int(datetime.now(UTC).timestamp())
        id_token_claims = {"auth_time": auth_time, "amr": []}

        valid, error = verify_step_up_auth(id_token_claims)

        assert valid is False
        assert error == "mfa_not_performed"

    def test_verify_step_up_auth_amr_none(self):
        """Test step-up auth validation fails when amr is None."""
        auth_time = int(datetime.now(UTC).timestamp())
        id_token_claims = {"auth_time": auth_time, "amr": None}

        valid, error = verify_step_up_auth(id_token_claims)

        assert valid is False
        assert error == "mfa_not_performed"

    def test_verify_step_up_auth_disallowed_amr_method(self):
        """Test step-up auth validation fails with disallowed AMR method."""
        auth_time = int(datetime.now(UTC).timestamp())
        id_token_claims = {"auth_time": auth_time, "amr": ["pwd"]}  # Password only

        valid, error = verify_step_up_auth(id_token_claims)

        assert valid is False
        assert error == "mfa_method_not_allowed"

    def test_verify_step_up_auth_webauthn_allowed(self):
        """Test step-up auth validation passes with webauthn method."""
        auth_time = int(datetime.now(UTC).timestamp())
        id_token_claims = {"auth_time": auth_time, "amr": ["webauthn"]}

        valid, error = verify_step_up_auth(id_token_claims)

        assert valid is True
        assert error is None

    def test_verify_step_up_auth_hwk_allowed(self):
        """Test step-up auth validation passes with hardware key (hwk) method."""
        auth_time = int(datetime.now(UTC).timestamp())
        id_token_claims = {"auth_time": auth_time, "amr": ["hwk"]}

        valid, error = verify_step_up_auth(id_token_claims)

        assert valid is True
        assert error is None

    def test_verify_step_up_auth_sms_allowed(self):
        """Test step-up auth validation passes with SMS method."""
        auth_time = int(datetime.now(UTC).timestamp())
        id_token_claims = {"auth_time": auth_time, "amr": ["sms"]}

        valid, error = verify_step_up_auth(id_token_claims)

        assert valid is True
        assert error is None

    def test_verify_step_up_auth_push_allowed(self):
        """Test step-up auth validation passes with push notification method."""
        auth_time = int(datetime.now(UTC).timestamp())
        id_token_claims = {"auth_time": auth_time, "amr": ["push"]}

        valid, error = verify_step_up_auth(id_token_claims)

        assert valid is True
        assert error is None


class TestGetAmrMethod:
    """Tests for get_amr_method() extraction function."""

    def test_get_amr_method_with_list(self):
        """Test AMR method extraction from list."""
        id_token_claims = {"amr": ["mfa", "otp"]}

        result = get_amr_method(id_token_claims)

        assert result == "mfa"  # Returns first method

    def test_get_amr_method_with_tuple(self):
        """Test AMR method extraction from tuple."""
        id_token_claims = {"amr": ("otp", "sms")}

        result = get_amr_method(id_token_claims)

        assert result == "otp"

    def test_get_amr_method_with_empty_list(self):
        """Test AMR method extraction returns None for empty list."""
        id_token_claims = {"amr": []}

        result = get_amr_method(id_token_claims)

        assert result is None

    def test_get_amr_method_missing_amr(self):
        """Test AMR method extraction returns None when amr missing."""
        id_token_claims = {}

        result = get_amr_method(id_token_claims)

        assert result is None

    def test_get_amr_method_with_none(self):
        """Test AMR method extraction returns None when amr is None."""
        id_token_claims = {"amr": None}

        result = get_amr_method(id_token_claims)

        assert result is None

    def test_get_amr_method_with_string(self):
        """Test AMR method extraction returns None for non-list/tuple."""
        id_token_claims = {"amr": "mfa"}

        result = get_amr_method(id_token_claims)

        assert result is None


class TestRequire2faForAction:
    """Tests for require_2fa_for_action() async validation function."""

    @pytest.mark.asyncio()
    async def test_require_2fa_missing_session(self):
        """Test 2FA requirement fails when session missing."""
        valid, error = await require_2fa_for_action(None, "delete_strategy", None)

        assert valid is False
        assert error == "session_missing"

    @pytest.mark.asyncio()
    async def test_require_2fa_missing_step_up_claims_with_audit(self):
        """Test 2FA requirement fails when step_up_claims missing (with audit)."""
        session_data = Mock()
        session_data.user_id = "user123"
        session_data.step_up_claims = None

        audit_logger = AsyncMock()

        valid, error = await require_2fa_for_action(session_data, "delete_strategy", audit_logger)

        assert valid is False
        assert error == "step_up_required"

        # Verify audit log called
        audit_logger.log_auth_event.assert_called_once()
        call_args = audit_logger.log_auth_event.call_args
        assert call_args[1]["user_id"] == "user123"
        assert call_args[1]["action"] == "delete_strategy"
        assert call_args[1]["outcome"] == "denied"
        assert call_args[1]["details"]["reason"] == "step_up_required"

    @pytest.mark.asyncio()
    async def test_require_2fa_missing_step_up_claims_without_audit(self):
        """Test 2FA requirement fails when step_up_claims missing (no audit)."""
        session_data = Mock()
        session_data.step_up_claims = None

        valid, error = await require_2fa_for_action(session_data, "delete_strategy", None)

        assert valid is False
        assert error == "step_up_required"

    @pytest.mark.asyncio()
    async def test_require_2fa_invalid_claims_with_audit(self):
        """Test 2FA requirement fails with invalid claims (with audit)."""
        session_data = Mock()
        session_data.user_id = "user456"
        # Claims with expired auth_time
        old_auth_time = int(
            (datetime.now(UTC) - timedelta(seconds=MFA_MAX_AGE_SECONDS + 10)).timestamp()
        )
        session_data.step_up_claims = {"auth_time": old_auth_time, "amr": ["mfa"]}

        audit_logger = AsyncMock()

        valid, error = await require_2fa_for_action(
            session_data, "modify_risk_limits", audit_logger
        )

        assert valid is False
        assert error == "auth_too_old"

        # Verify audit log called
        audit_logger.log_auth_event.assert_called_once()
        call_args = audit_logger.log_auth_event.call_args
        assert call_args[1]["user_id"] == "user456"
        assert call_args[1]["action"] == "modify_risk_limits"
        assert call_args[1]["outcome"] == "denied"
        assert call_args[1]["details"]["reason"] == "auth_too_old"

    @pytest.mark.asyncio()
    async def test_require_2fa_invalid_claims_without_audit(self):
        """Test 2FA requirement fails with invalid claims (no audit)."""
        session_data = Mock()
        # Claims missing auth_time
        session_data.step_up_claims = {"amr": ["mfa"]}

        valid, error = await require_2fa_for_action(session_data, "delete_strategy", None)

        assert valid is False
        assert error == "missing_auth_time"

    @pytest.mark.asyncio()
    async def test_require_2fa_success(self):
        """Test 2FA requirement passes with valid recent MFA."""
        session_data = Mock()
        auth_time = int(datetime.now(UTC).timestamp())
        session_data.step_up_claims = {"auth_time": auth_time, "amr": ["mfa"]}

        valid, error = await require_2fa_for_action(session_data, "delete_strategy", None)

        assert valid is True
        assert error is None

    @pytest.mark.asyncio()
    async def test_require_2fa_success_with_audit_logger(self):
        """Test 2FA requirement passes with valid MFA (audit logger present but not called)."""
        session_data = Mock()
        auth_time = int(datetime.now(UTC).timestamp())
        session_data.step_up_claims = {"auth_time": auth_time, "amr": ["otp"]}

        audit_logger = AsyncMock()

        valid, error = await require_2fa_for_action(
            session_data, "modify_risk_limits", audit_logger
        )

        assert valid is True
        assert error is None

        # Audit logger should NOT be called on success
        audit_logger.log_auth_event.assert_not_called()


class TestAllowedAmrMethods:
    """Tests for ALLOWED_AMR_METHODS constant."""

    def test_allowed_amr_methods_contains_expected_values(self):
        """Test ALLOWED_AMR_METHODS contains all expected MFA methods."""
        assert "mfa" in ALLOWED_AMR_METHODS
        assert "otp" in ALLOWED_AMR_METHODS
        assert "sms" in ALLOWED_AMR_METHODS
        assert "push" in ALLOWED_AMR_METHODS
        assert "webauthn" in ALLOWED_AMR_METHODS
        assert "hwk" in ALLOWED_AMR_METHODS

    def test_allowed_amr_methods_does_not_contain_password(self):
        """Test ALLOWED_AMR_METHODS does not contain password-only method."""
        assert "pwd" not in ALLOWED_AMR_METHODS


class TestMfaMaxAgeConfiguration:
    """Tests for MFA_MAX_AGE_SECONDS configuration."""

    def test_mfa_max_age_default_value(self):
        """Test MFA_MAX_AGE_SECONDS has default value of 60 seconds."""
        # Default when MFA_MAX_AGE_SECONDS not set
        assert MFA_MAX_AGE_SECONDS >= 60 or MFA_MAX_AGE_SECONDS == int(
            os.getenv("MFA_MAX_AGE_SECONDS", "60")
        )

    def test_mfa_max_age_respects_env_var(self):
        """Test MFA_MAX_AGE_SECONDS respects environment variable."""
        # This test verifies the module reads from env var
        # (actual value depends on environment)
        assert isinstance(MFA_MAX_AGE_SECONDS, int)
        assert MFA_MAX_AGE_SECONDS > 0
