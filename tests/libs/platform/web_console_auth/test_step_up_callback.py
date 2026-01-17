"""
Unit tests for libs.platform.web_console_auth.step_up_callback.

Tests cover:
- handle_step_up_callback() error paths (session not found, db unavailable, version mismatch, timeout, etc.)
- handle_step_up_callback() success path (valid MFA step-up)
- State validation (missing validator, invalid state)
- Token exchange (missing exchange_code, missing id_token)
- JWKS validation (missing validator, validation failure)
- Claims validation (subject mismatch, step-up auth failure)
- Audit logging at all decision points
- Error message mapping
- clear_step_up_state() wrapper

Target: 85%+ branch coverage (baseline from 0%)
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch

import jwt
import pytest

from libs.platform.web_console_auth.step_up_callback import (
    STEP_UP_CALLBACK_TIMEOUT_SECONDS,
    SecurityError,
    clear_step_up_state,
    handle_step_up_callback,
)


class TestHandleStepUpCallbackErrorPaths:
    """Tests for handle_step_up_callback() error handling paths."""

    @pytest.mark.asyncio()
    async def test_session_not_found(self):
        """Test handle_step_up_callback() returns error when session missing."""
        mock_session_store = AsyncMock()
        mock_session_store.get_session = AsyncMock(return_value=None)
        mock_session_store.clear_step_up_state = AsyncMock()
        mock_audit_logger = AsyncMock()

        result = await handle_step_up_callback(
            code="test_code",
            state="test_state",
            session_store=mock_session_store,
            session_id="session123",
            audit_logger=mock_audit_logger,
        )

        assert result["error"] == "session_not_found"
        assert result["redirect_to"] == "/login"
        mock_session_store.clear_step_up_state.assert_called_once_with("session123")
        mock_audit_logger.log_auth_event.assert_called_once()
        audit_call = mock_audit_logger.log_auth_event.call_args[1]
        assert audit_call["user_id"] is None
        assert audit_call["outcome"] == "denied"
        assert audit_call["details"]["reason"] == "session_not_found"

    @pytest.mark.asyncio()
    async def test_session_not_found_without_audit_logger(self):
        """Test handle_step_up_callback() handles missing audit_logger gracefully."""
        mock_session_store = AsyncMock()
        mock_session_store.get_session = AsyncMock(return_value=None)
        mock_session_store.clear_step_up_state = AsyncMock()

        result = await handle_step_up_callback(
            code="test_code",
            state="test_state",
            session_store=mock_session_store,
            session_id="session123",
            audit_logger=None,
        )

        assert result["error"] == "session_not_found"
        # Should not raise exception with audit_logger=None

    @pytest.mark.asyncio()
    async def test_db_pool_unavailable_fail_closed(self):
        """Test handle_step_up_callback() fails closed when db_pool unavailable."""
        mock_session_data = Mock()
        mock_session_data.user_id = "user123"
        mock_session_data.session_version = 1

        mock_session_store = AsyncMock()
        mock_session_store.get_session = AsyncMock(return_value=mock_session_data)
        mock_session_store.delete_session = AsyncMock()
        mock_session_store.clear_step_up_state = AsyncMock()
        mock_audit_logger = AsyncMock()

        result = await handle_step_up_callback(
            code="test_code",
            state="test_state",
            session_store=mock_session_store,
            session_id="session123",
            audit_logger=mock_audit_logger,
            db_pool=None,  # Fail-closed: missing db_pool
        )

        assert result["error"] == "session_validation_unavailable"
        assert result["redirect_to"] == "/login"
        mock_session_store.delete_session.assert_called_once_with("session123")
        mock_session_store.clear_step_up_state.assert_called_once_with("session123")
        audit_call = mock_audit_logger.log_auth_event.call_args[1]
        assert audit_call["user_id"] == "user123"
        assert audit_call["details"]["reason"] == "db_pool_unavailable"

    @pytest.mark.asyncio()
    async def test_session_version_mismatch(self):
        """Test handle_step_up_callback() invalidates session on version mismatch."""
        mock_session_data = Mock()
        mock_session_data.user_id = "user456"
        mock_session_data.session_version = 2

        mock_session_store = AsyncMock()
        mock_session_store.get_session = AsyncMock(return_value=mock_session_data)
        mock_session_store.delete_session = AsyncMock()
        mock_session_store.clear_step_up_state = AsyncMock()
        mock_audit_logger = AsyncMock()
        mock_db_pool = Mock()

        # Mock validate_session_version to return False (mismatch)
        with patch(
            "libs.platform.web_console_auth.step_up_callback.validate_session_version",
            return_value=False,
        ):
            result = await handle_step_up_callback(
                code="test_code",
                state="test_state",
                session_store=mock_session_store,
                session_id="session456",
                audit_logger=mock_audit_logger,
                db_pool=mock_db_pool,
            )

        assert result["error"] == "session_invalidated"
        assert result["redirect_to"] == "/login"
        mock_session_store.delete_session.assert_called_once_with("session456")
        mock_session_store.clear_step_up_state.assert_called_once_with("session456")
        audit_call = mock_audit_logger.log_auth_event.call_args[1]
        assert audit_call["action"] == "step_up_session_invalidated"
        assert audit_call["details"]["reason"] == "session_version_mismatch"

    @pytest.mark.asyncio()
    async def test_step_up_timeout_exceeded(self):
        """Test handle_step_up_callback() times out after 300 seconds."""
        mock_session_data = Mock()
        mock_session_data.user_id = "user789"
        mock_session_data.session_version = 1
        # Set step_up_requested_at to 400 seconds ago (exceeds 300s timeout)
        mock_session_data.step_up_requested_at = datetime.now(UTC) - timedelta(seconds=400)

        mock_session_store = AsyncMock()
        mock_session_store.get_session = AsyncMock(return_value=mock_session_data)
        mock_session_store.clear_step_up_state = AsyncMock()
        mock_audit_logger = AsyncMock()
        mock_db_pool = Mock()

        with patch(
            "libs.platform.web_console_auth.step_up_callback.validate_session_version",
            return_value=True,
        ):
            result = await handle_step_up_callback(
                code="test_code",
                state="test_state",
                session_store=mock_session_store,
                session_id="session789",
                audit_logger=mock_audit_logger,
                db_pool=mock_db_pool,
            )

        assert result["error"] == "step_up_timeout"
        assert result["redirect_to"] == "/dashboard"
        mock_session_store.clear_step_up_state.assert_called_once_with("session789")
        audit_call = mock_audit_logger.log_auth_event.call_args[1]
        assert audit_call["action"] == "step_up_timeout"
        assert audit_call["details"]["elapsed_seconds"] > 300

    @pytest.mark.asyncio()
    async def test_step_up_within_timeout(self):
        """Test handle_step_up_callback() allows step-up within 300 seconds (no timeout)."""
        mock_session_data = Mock()
        mock_session_data.user_id = "user999"
        mock_session_data.session_version = 1
        # Set step_up_requested_at to 100 seconds ago (within 300s timeout)
        mock_session_data.step_up_requested_at = datetime.now(UTC) - timedelta(seconds=100)
        mock_session_data.pending_action = "/risk"

        mock_session_store = AsyncMock()
        mock_session_store.get_session = AsyncMock(return_value=mock_session_data)
        mock_session_store.clear_step_up_state = AsyncMock()
        mock_db_pool = Mock()

        # Mock validate_state to return False (will fail before success path)
        mock_validate_state = Mock(return_value=False)

        with patch(
            "libs.platform.web_console_auth.step_up_callback.validate_session_version",
            return_value=True,
        ):
            result = await handle_step_up_callback(
                code="test_code",
                state="test_state",
                session_store=mock_session_store,
                session_id="session999",
                audit_logger=None,
                db_pool=mock_db_pool,
                validate_state=mock_validate_state,
            )

        # Should proceed past timeout check (failed on state validation instead)
        assert result["error"] == "invalid_state"

    @pytest.mark.asyncio()
    async def test_missing_validate_state_function(self):
        """Test handle_step_up_callback() requires validate_state function."""
        mock_session_data = Mock()
        mock_session_data.user_id = "user111"
        mock_session_data.session_version = 1
        mock_session_data.step_up_requested_at = None
        mock_session_data.pending_action = "/alerts"

        mock_session_store = AsyncMock()
        mock_session_store.get_session = AsyncMock(return_value=mock_session_data)
        mock_session_store.clear_step_up_state = AsyncMock()
        mock_audit_logger = AsyncMock()
        mock_db_pool = Mock()

        with patch(
            "libs.platform.web_console_auth.step_up_callback.validate_session_version",
            return_value=True,
        ):
            result = await handle_step_up_callback(
                code="test_code",
                state="test_state",
                session_store=mock_session_store,
                session_id="session111",
                audit_logger=mock_audit_logger,
                db_pool=mock_db_pool,
                validate_state=None,  # Missing validator
            )

        assert result["error"] == "state_validation_required"
        assert result["redirect_to"] == "/alerts"
        audit_call = mock_audit_logger.log_auth_event.call_args[1]
        assert audit_call["details"]["reason"] == "missing_validator"
        assert audit_call["details"]["pending_action"] == "/alerts"

    @pytest.mark.asyncio()
    async def test_invalid_state_fails_validation(self):
        """Test handle_step_up_callback() rejects invalid state parameter."""
        mock_session_data = Mock()
        mock_session_data.user_id = "user222"
        mock_session_data.session_version = 1
        mock_session_data.step_up_requested_at = None
        mock_session_data.pending_action = None  # No pending action

        mock_session_store = AsyncMock()
        mock_session_store.get_session = AsyncMock(return_value=mock_session_data)
        mock_session_store.clear_step_up_state = AsyncMock()
        mock_audit_logger = AsyncMock()
        mock_db_pool = Mock()

        mock_validate_state = Mock(return_value=False)  # State invalid

        with patch(
            "libs.platform.web_console_auth.step_up_callback.validate_session_version",
            return_value=True,
        ):
            result = await handle_step_up_callback(
                code="test_code",
                state="bad_state",
                session_store=mock_session_store,
                session_id="session222",
                audit_logger=mock_audit_logger,
                db_pool=mock_db_pool,
                validate_state=mock_validate_state,
            )

        assert result["error"] == "invalid_state"
        assert result["redirect_to"] == "/login"  # Falls back to /login when no pending_action
        mock_validate_state.assert_called_once_with("bad_state", "session222")
        audit_call = mock_audit_logger.log_auth_event.call_args[1]
        assert audit_call["details"]["reason"] == "state_mismatch"

    @pytest.mark.asyncio()
    async def test_missing_exchange_code_function(self):
        """Test handle_step_up_callback() requires exchange_code function."""
        mock_session_data = Mock()
        mock_session_data.user_id = "user333"
        mock_session_data.session_version = 1
        mock_session_data.step_up_requested_at = None
        mock_session_data.pending_action = "/dashboard"

        mock_session_store = AsyncMock()
        mock_session_store.get_session = AsyncMock(return_value=mock_session_data)
        mock_session_store.clear_step_up_state = AsyncMock()
        mock_audit_logger = AsyncMock()
        mock_db_pool = Mock()
        mock_validate_state = Mock(return_value=True)

        with patch(
            "libs.platform.web_console_auth.step_up_callback.validate_session_version",
            return_value=True,
        ):
            result = await handle_step_up_callback(
                code="test_code",
                state="valid_state",
                session_store=mock_session_store,
                session_id="session333",
                audit_logger=mock_audit_logger,
                db_pool=mock_db_pool,
                validate_state=mock_validate_state,
                exchange_code=None,  # Missing exchange_code
            )

        assert result["error"] == "step_up_configuration_error"
        assert result["redirect_to"] == "/dashboard"
        audit_call = mock_audit_logger.log_auth_event.call_args[1]
        assert audit_call["details"]["reason"] == "exchange_code_missing"

    @pytest.mark.asyncio()
    async def test_missing_jwks_validator(self):
        """Test handle_step_up_callback() requires JWKS validator."""
        mock_session_data = Mock()
        mock_session_data.user_id = "user444"
        mock_session_data.session_version = 1
        mock_session_data.step_up_requested_at = None
        mock_session_data.pending_action = None

        mock_session_store = AsyncMock()
        mock_session_store.get_session = AsyncMock(return_value=mock_session_data)
        mock_session_store.clear_step_up_state = AsyncMock()
        mock_audit_logger = AsyncMock()
        mock_db_pool = Mock()
        mock_validate_state = Mock(return_value=True)
        mock_exchange_code = AsyncMock()

        with patch(
            "libs.platform.web_console_auth.step_up_callback.validate_session_version",
            return_value=True,
        ):
            result = await handle_step_up_callback(
                code="test_code",
                state="valid_state",
                session_store=mock_session_store,
                session_id="session444",
                audit_logger=mock_audit_logger,
                db_pool=mock_db_pool,
                validate_state=mock_validate_state,
                exchange_code=mock_exchange_code,
                jwks_validator=None,  # Missing validator
            )

        assert result["error"] == "step_up_configuration_error"
        assert result["redirect_to"] == "/login"
        audit_call = mock_audit_logger.log_auth_event.call_args[1]
        assert audit_call["details"]["reason"] == "jwks_validator_missing"

    @pytest.mark.asyncio()
    async def test_missing_expected_audience(self):
        """Test handle_step_up_callback() requires expected_audience."""
        mock_session_data = Mock()
        mock_session_data.user_id = "user555"
        mock_session_data.session_version = 1
        mock_session_data.step_up_requested_at = None
        mock_session_data.pending_action = "/risk"

        mock_session_store = AsyncMock()
        mock_session_store.get_session = AsyncMock(return_value=mock_session_data)
        mock_session_store.clear_step_up_state = AsyncMock()
        mock_audit_logger = AsyncMock()
        mock_db_pool = Mock()
        mock_validate_state = Mock(return_value=True)
        mock_exchange_code = AsyncMock()
        mock_jwks_validator = Mock()
        mock_jwks_validator.auth0_domain = "test.auth0.com"

        with patch(
            "libs.platform.web_console_auth.step_up_callback.validate_session_version",
            return_value=True,
        ):
            result = await handle_step_up_callback(
                code="test_code",
                state="valid_state",
                session_store=mock_session_store,
                session_id="session555",
                audit_logger=mock_audit_logger,
                db_pool=mock_db_pool,
                validate_state=mock_validate_state,
                exchange_code=mock_exchange_code,
                jwks_validator=mock_jwks_validator,
                expected_audience=None,  # Missing audience
                expected_issuer="https://test.auth0.com/",
            )

        assert result["error"] == "step_up_configuration_error"
        audit_call = mock_audit_logger.log_auth_event.call_args[1]
        assert audit_call["details"]["reason"] == "issuer_or_audience_missing"
        assert not audit_call["details"]["has_expected_audience"]

    @pytest.mark.asyncio()
    async def test_missing_issuer_no_auth0_domain(self):
        """Test handle_step_up_callback() fails when issuer cannot be derived."""
        mock_session_data = Mock()
        mock_session_data.user_id = "user666"
        mock_session_data.session_version = 1
        mock_session_data.step_up_requested_at = None
        mock_session_data.pending_action = None

        mock_session_store = AsyncMock()
        mock_session_store.get_session = AsyncMock(return_value=mock_session_data)
        mock_session_store.clear_step_up_state = AsyncMock()
        mock_audit_logger = AsyncMock()
        mock_db_pool = Mock()
        mock_validate_state = Mock(return_value=True)
        mock_exchange_code = AsyncMock()
        mock_jwks_validator = Mock(spec=[])  # No auth0_domain attribute

        with patch(
            "libs.platform.web_console_auth.step_up_callback.validate_session_version",
            return_value=True,
        ):
            result = await handle_step_up_callback(
                code="test_code",
                state="valid_state",
                session_store=mock_session_store,
                session_id="session666",
                audit_logger=mock_audit_logger,
                db_pool=mock_db_pool,
                validate_state=mock_validate_state,
                exchange_code=mock_exchange_code,
                jwks_validator=mock_jwks_validator,
                expected_audience="test-audience",
                expected_issuer=None,  # Missing issuer, and validator has no auth0_domain
            )

        assert result["error"] == "step_up_configuration_error"
        audit_call = mock_audit_logger.log_auth_event.call_args[1]
        assert not audit_call["details"]["has_issuer"]

    @pytest.mark.asyncio()
    async def test_missing_id_token_in_exchange_response(self):
        """Test handle_step_up_callback() fails when id_token missing from token exchange."""
        mock_session_data = Mock()
        mock_session_data.user_id = "user777"
        mock_session_data.session_version = 1
        mock_session_data.step_up_requested_at = None
        mock_session_data.pending_action = "/dashboard"

        mock_session_store = AsyncMock()
        mock_session_store.get_session = AsyncMock(return_value=mock_session_data)
        mock_session_store.clear_step_up_state = AsyncMock()
        mock_audit_logger = AsyncMock()
        mock_db_pool = Mock()
        mock_validate_state = Mock(return_value=True)
        mock_exchange_code = AsyncMock(return_value={"access_token": "xxx"})  # No id_token
        mock_jwks_validator = Mock()
        mock_jwks_validator.auth0_domain = "test.auth0.com"

        with patch(
            "libs.platform.web_console_auth.step_up_callback.validate_session_version",
            return_value=True,
        ):
            result = await handle_step_up_callback(
                code="test_code",
                state="valid_state",
                session_store=mock_session_store,
                session_id="session777",
                audit_logger=mock_audit_logger,
                db_pool=mock_db_pool,
                validate_state=mock_validate_state,
                exchange_code=mock_exchange_code,
                jwks_validator=mock_jwks_validator,
                expected_audience="test-aud",
                expected_issuer="https://test.auth0.com/",
            )

        assert result["error"] == "id_token_missing"
        assert result["redirect_to"] == "/dashboard"
        audit_call = mock_audit_logger.log_auth_event.call_args[1]
        assert audit_call["details"]["reason"] == "id_token_missing"

    @pytest.mark.asyncio()
    async def test_id_token_validation_fails(self):
        """Test handle_step_up_callback() handles JWT validation failures."""
        mock_session_data = Mock()
        mock_session_data.user_id = "user888"
        mock_session_data.session_version = 1
        mock_session_data.step_up_requested_at = None
        mock_session_data.pending_action = None

        mock_session_store = AsyncMock()
        mock_session_store.get_session = AsyncMock(return_value=mock_session_data)
        mock_session_store.clear_step_up_state = AsyncMock()
        mock_audit_logger = AsyncMock()
        mock_db_pool = Mock()
        mock_validate_state = Mock(return_value=True)
        mock_exchange_code = AsyncMock(return_value={"id_token": "invalid.jwt.token"})
        mock_jwks_validator = Mock()
        mock_jwks_validator.auth0_domain = "test.auth0.com"
        mock_jwks_validator.validate_id_token = AsyncMock(
            side_effect=jwt.InvalidTokenError("Token expired")
        )

        with patch(
            "libs.platform.web_console_auth.step_up_callback.validate_session_version",
            return_value=True,
        ):
            result = await handle_step_up_callback(
                code="test_code",
                state="valid_state",
                session_store=mock_session_store,
                session_id="session888",
                audit_logger=mock_audit_logger,
                db_pool=mock_db_pool,
                validate_state=mock_validate_state,
                exchange_code=mock_exchange_code,
                jwks_validator=mock_jwks_validator,
                expected_audience="test-aud",
                expected_issuer="https://test.auth0.com/",
            )

        assert result["error"] == "id_token_validation_failed"
        assert result["redirect_to"] == "/dashboard"
        audit_call = mock_audit_logger.log_auth_event.call_args[1]
        assert audit_call["details"]["reason"] == "id_token_validation_failed"
        assert audit_call["details"]["error_type"] == "InvalidTokenError"

    @pytest.mark.asyncio()
    async def test_subject_mismatch(self):
        """Test handle_step_up_callback() detects subject mismatch (different user)."""
        mock_session_data = Mock()
        mock_session_data.user_id = "user999"
        mock_session_data.session_version = 1
        mock_session_data.step_up_requested_at = None

        mock_session_store = AsyncMock()
        mock_session_store.get_session = AsyncMock(return_value=mock_session_data)
        mock_session_store.delete_session = AsyncMock()
        mock_session_store.clear_step_up_state = AsyncMock()
        mock_audit_logger = AsyncMock()
        mock_db_pool = Mock()
        mock_validate_state = Mock(return_value=True)
        mock_exchange_code = AsyncMock(return_value={"id_token": "valid.jwt.token"})
        mock_jwks_validator = Mock()
        mock_jwks_validator.auth0_domain = "test.auth0.com"
        mock_jwks_validator.validate_id_token = AsyncMock(
            return_value={"sub": "different_user", "auth_time": 1234567890, "amr": ["mfa"]}
        )

        with patch(
            "libs.platform.web_console_auth.step_up_callback.validate_session_version",
            return_value=True,
        ):
            result = await handle_step_up_callback(
                code="test_code",
                state="valid_state",
                session_store=mock_session_store,
                session_id="session999",
                audit_logger=mock_audit_logger,
                db_pool=mock_db_pool,
                validate_state=mock_validate_state,
                exchange_code=mock_exchange_code,
                jwks_validator=mock_jwks_validator,
                expected_audience="test-aud",
                expected_issuer="https://test.auth0.com/",
            )

        assert result["error"] == "subject_mismatch"
        assert result["redirect_to"] == "/login"
        mock_session_store.delete_session.assert_called_once_with("session999")
        mock_session_store.clear_step_up_state.assert_called_once_with("session999")
        audit_call = mock_audit_logger.log_auth_event.call_args[1]
        assert audit_call["details"]["error"] == "subject_mismatch"
        assert audit_call["details"]["expected"] == "user999"
        assert audit_call["details"]["received"] == "different_user"

    @pytest.mark.asyncio()
    async def test_step_up_auth_verification_fails(self):
        """Test handle_step_up_callback() fails when MFA verification invalid."""
        mock_session_data = Mock()
        mock_session_data.user_id = "user101"
        mock_session_data.session_version = 1
        mock_session_data.step_up_requested_at = None
        mock_session_data.pending_action = None

        mock_session_store = AsyncMock()
        mock_session_store.get_session = AsyncMock(return_value=mock_session_data)
        mock_session_store.clear_step_up_state = AsyncMock()
        mock_audit_logger = AsyncMock()
        mock_db_pool = Mock()
        mock_validate_state = Mock(return_value=True)
        mock_exchange_code = AsyncMock(return_value={"id_token": "valid.jwt.token"})
        mock_jwks_validator = Mock()
        mock_jwks_validator.auth0_domain = "test.auth0.com"
        mock_jwks_validator.validate_id_token = AsyncMock(
            return_value={"sub": "user101", "auth_time": 1234567890, "amr": ["pwd"]}  # No MFA
        )

        with patch(
            "libs.platform.web_console_auth.step_up_callback.validate_session_version",
            return_value=True,
        ):
            with patch(
                "libs.platform.web_console_auth.step_up_callback.verify_step_up_auth",
                return_value=(False, "mfa_not_performed"),
            ):
                result = await handle_step_up_callback(
                    code="test_code",
                    state="valid_state",
                    session_store=mock_session_store,
                    session_id="session101",
                    audit_logger=mock_audit_logger,
                    db_pool=mock_db_pool,
                    validate_state=mock_validate_state,
                    exchange_code=mock_exchange_code,
                    jwks_validator=mock_jwks_validator,
                    expected_audience="test-aud",
                    expected_issuer="https://test.auth0.com/",
                )

        assert result["error"] == "mfa_not_performed"
        assert result["redirect_to"] == "/dashboard"
        assert "Multi-factor authentication was not completed" in result["message"]
        audit_call = mock_audit_logger.log_auth_event.call_args[1]
        assert audit_call["details"]["error"] == "mfa_not_performed"


class TestHandleStepUpCallbackSuccess:
    """Tests for handle_step_up_callback() success path."""

    @pytest.mark.asyncio()
    async def test_step_up_success_with_pending_action(self):
        """Test handle_step_up_callback() succeeds and redirects to pending action."""
        mock_session_data = Mock()
        mock_session_data.user_id = "user_success"
        mock_session_data.session_version = 1
        mock_session_data.step_up_requested_at = datetime.now(UTC) - timedelta(seconds=10)
        mock_session_data.pending_action = "/alerts/acknowledge"

        mock_session_store = AsyncMock()
        mock_session_store.get_session = AsyncMock(return_value=mock_session_data)
        mock_session_store.update_step_up_claims = AsyncMock()
        mock_session_store.clear_step_up_request_timestamp = AsyncMock()
        mock_audit_logger = AsyncMock()
        mock_db_pool = Mock()
        mock_validate_state = Mock(return_value=True)
        mock_exchange_code = AsyncMock(return_value={"id_token": "valid.jwt.token"})
        mock_jwks_validator = Mock()
        mock_jwks_validator.auth0_domain = "test.auth0.com"
        mock_id_token_claims = {
            "sub": "user_success",
            "auth_time": int(datetime.now(UTC).timestamp()),
            "amr": ["mfa", "otp"],
        }
        mock_jwks_validator.validate_id_token = AsyncMock(return_value=mock_id_token_claims)

        with patch(
            "libs.platform.web_console_auth.step_up_callback.validate_session_version",
            return_value=True,
        ):
            with patch(
                "libs.platform.web_console_auth.step_up_callback.verify_step_up_auth",
                return_value=(True, None),
            ):
                result = await handle_step_up_callback(
                    code="valid_code",
                    state="valid_state",
                    session_store=mock_session_store,
                    session_id="session_success",
                    audit_logger=mock_audit_logger,
                    db_pool=mock_db_pool,
                    validate_state=mock_validate_state,
                    exchange_code=mock_exchange_code,
                    jwks_validator=mock_jwks_validator,
                    expected_audience="test-aud",
                    expected_issuer="https://test.auth0.com/",
                )

        assert "error" not in result
        assert result["redirect_to"] == "/alerts/acknowledge"
        mock_session_store.update_step_up_claims.assert_called_once_with(
            "session_success", mock_id_token_claims
        )
        mock_session_store.clear_step_up_request_timestamp.assert_called_once_with("session_success")
        audit_call = mock_audit_logger.log_auth_event.call_args[1]
        assert audit_call["action"] == "step_up_success"
        assert audit_call["outcome"] == "success"

    @pytest.mark.asyncio()
    async def test_step_up_success_defaults_to_dashboard(self):
        """Test handle_step_up_callback() defaults to /dashboard when no pending action."""
        mock_session_data = Mock()
        mock_session_data.user_id = "user_default"
        mock_session_data.session_version = 1
        mock_session_data.step_up_requested_at = None
        mock_session_data.pending_action = None  # No pending action

        mock_session_store = AsyncMock()
        mock_session_store.get_session = AsyncMock(return_value=mock_session_data)
        mock_session_store.update_step_up_claims = AsyncMock()
        mock_session_store.clear_step_up_request_timestamp = AsyncMock()
        mock_db_pool = Mock()
        mock_validate_state = Mock(return_value=True)
        mock_exchange_code = AsyncMock(return_value={"id_token": "valid.jwt.token"})
        mock_jwks_validator = Mock()
        mock_jwks_validator.auth0_domain = "test.auth0.com"
        mock_id_token_claims = {
            "sub": "user_default",
            "auth_time": int(datetime.now(UTC).timestamp()),
            "amr": ["mfa"],
        }
        mock_jwks_validator.validate_id_token = AsyncMock(return_value=mock_id_token_claims)

        with patch(
            "libs.platform.web_console_auth.step_up_callback.validate_session_version",
            return_value=True,
        ):
            with patch(
                "libs.platform.web_console_auth.step_up_callback.verify_step_up_auth",
                return_value=(True, None),
            ):
                result = await handle_step_up_callback(
                    code="valid_code",
                    state="valid_state",
                    session_store=mock_session_store,
                    session_id="session_default",
                    audit_logger=None,  # No audit logger
                    db_pool=mock_db_pool,
                    validate_state=mock_validate_state,
                    exchange_code=mock_exchange_code,
                    jwks_validator=mock_jwks_validator,
                    expected_audience="test-aud",
                    expected_issuer="https://test.auth0.com/",
                )

        assert result["redirect_to"] == "/dashboard"

    @pytest.mark.asyncio()
    async def test_issuer_derived_from_jwks_validator(self):
        """Test handle_step_up_callback() derives issuer from jwks_validator.auth0_domain."""
        mock_session_data = Mock()
        mock_session_data.user_id = "user_derived"
        mock_session_data.session_version = 1
        mock_session_data.step_up_requested_at = None
        mock_session_data.pending_action = "/dashboard"

        mock_session_store = AsyncMock()
        mock_session_store.get_session = AsyncMock(return_value=mock_session_data)
        mock_session_store.update_step_up_claims = AsyncMock()
        mock_session_store.clear_step_up_request_timestamp = AsyncMock()
        mock_db_pool = Mock()
        mock_validate_state = Mock(return_value=True)
        mock_exchange_code = AsyncMock(return_value={"id_token": "valid.jwt.token"})
        mock_jwks_validator = Mock()
        mock_jwks_validator.auth0_domain = "derived.auth0.com"
        mock_id_token_claims = {
            "sub": "user_derived",
            "auth_time": int(datetime.now(UTC).timestamp()),
            "amr": ["webauthn"],
        }
        mock_jwks_validator.validate_id_token = AsyncMock(return_value=mock_id_token_claims)

        with patch(
            "libs.platform.web_console_auth.step_up_callback.validate_session_version",
            return_value=True,
        ):
            with patch(
                "libs.platform.web_console_auth.step_up_callback.verify_step_up_auth",
                return_value=(True, None),
            ):
                await handle_step_up_callback(
                    code="valid_code",
                    state="valid_state",
                    session_store=mock_session_store,
                    session_id="session_derived",
                    audit_logger=None,
                    db_pool=mock_db_pool,
                    validate_state=mock_validate_state,
                    exchange_code=mock_exchange_code,
                    jwks_validator=mock_jwks_validator,
                    expected_audience="test-aud",
                    expected_issuer=None,  # Will be derived from jwks_validator
                )

        # Verify validator called with derived issuer
        mock_jwks_validator.validate_id_token.assert_called_once()
        call_kwargs = mock_jwks_validator.validate_id_token.call_args[1]
        assert call_kwargs["expected_issuer"] == "https://derived.auth0.com/"


class TestClearStepUpState:
    """Tests for clear_step_up_state() wrapper function."""

    @pytest.mark.asyncio()
    async def test_clear_step_up_state_wrapper(self):
        """Test clear_step_up_state() delegates to session_store."""
        mock_session_store = AsyncMock()
        mock_session_store.clear_step_up_state = AsyncMock(return_value=True)

        result = await clear_step_up_state(mock_session_store, "session123")

        assert result is True
        mock_session_store.clear_step_up_state.assert_called_once_with("session123")


class TestErrorMessage:
    """Tests for _error_message() helper function."""

    def test_error_message_auth_too_old(self):
        """Test _error_message() returns specific message for auth_too_old."""
        from libs.platform.web_console_auth.step_up_callback import _error_message

        result = _error_message("auth_too_old")
        assert result == "Recent MFA is required. Please try again."

    def test_error_message_mfa_not_performed(self):
        """Test _error_message() returns specific message for mfa_not_performed."""
        from libs.platform.web_console_auth.step_up_callback import _error_message

        result = _error_message("mfa_not_performed")
        assert result == "Multi-factor authentication was not completed."

    def test_error_message_default(self):
        """Test _error_message() returns default message for unknown errors."""
        from libs.platform.web_console_auth.step_up_callback import _error_message

        result = _error_message("unknown_error")
        assert result == "Step-up authentication failed."

    def test_error_message_none(self):
        """Test _error_message() handles None error."""
        from libs.platform.web_console_auth.step_up_callback import _error_message

        result = _error_message(None)
        assert result == "Step-up authentication failed."


class TestSecurityError:
    """Tests for SecurityError custom exception."""

    def test_security_error_instantiation(self):
        """Test SecurityError can be raised and caught."""
        with pytest.raises(SecurityError, match="Test security error"):
            raise SecurityError("Test security error")


class TestStepUpCallbackTimeoutConstant:
    """Tests for STEP_UP_CALLBACK_TIMEOUT_SECONDS constant."""

    def test_timeout_constant_value(self):
        """Test STEP_UP_CALLBACK_TIMEOUT_SECONDS is set to 300 seconds."""
        assert STEP_UP_CALLBACK_TIMEOUT_SECONDS == 300
