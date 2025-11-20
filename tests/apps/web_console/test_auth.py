"""
Tests for Web Console Authentication.

Tests authentication flows, session management, and timeout enforcement.
"""

import time
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from apps.web_console import auth


class TestDevAuth:
    """Test dev mode authentication."""

    def test_init_session(self):
        """Test session initialization."""
        # Mock streamlit session_state
        mock_session_state = {}
        username = "test_user"
        auth_method = "dev"

        with patch("apps.web_console.auth.st.session_state", mock_session_state):
            auth._init_session(username, auth_method)

        # Verify session initialized
        assert mock_session_state["authenticated"] is True
        assert mock_session_state["username"] == username
        assert mock_session_state["auth_method"] == auth_method
        assert "login_time" in mock_session_state
        assert "last_activity" in mock_session_state
        assert "session_id" in mock_session_state

    def test_session_timeout_idle(self):
        """Test session timeout after idle period."""
        # Create session that's been idle too long
        mock_session_state = {
            "authenticated": True,
            "username": "test_user",
            "login_time": datetime.now(),
            "last_activity": datetime.now() - timedelta(minutes=20),  # 20 min idle
        }

        with patch("apps.web_console.auth.st.session_state", mock_session_state):
            with patch("apps.web_console.auth.SESSION_TIMEOUT_MINUTES", 15):
                result = auth._check_session_timeout()

        assert result is False  # Session should be expired

    def test_session_timeout_absolute(self):
        """Test session timeout after absolute time limit."""
        # Create session that's been active too long
        mock_session_state = {
            "authenticated": True,
            "username": "test_user",
            "login_time": datetime.now() - timedelta(hours=5),  # 5 hours old
            "last_activity": datetime.now(),
        }

        with patch("apps.web_console.auth.st.session_state", mock_session_state):
            with patch("apps.web_console.auth.SESSION_ABSOLUTE_TIMEOUT_HOURS", 4):
                result = auth._check_session_timeout()

        assert result is False  # Session should be expired

    def test_session_valid(self):
        """Test valid session passes timeout check."""
        # Create fresh session
        mock_session_state = {
            "authenticated": True,
            "username": "test_user",
            "login_time": datetime.now(),
            "last_activity": datetime.now(),
        }

        with patch("apps.web_console.auth.st.session_state", mock_session_state):
            result = auth._check_session_timeout()

        assert result is True  # Session should be valid
        # last_activity should be updated
        assert (datetime.now() - mock_session_state["last_activity"]).total_seconds() < 1

    def test_generate_session_id_unique(self):
        """Test session ID generation produces unique IDs."""
        username = "test_user"
        login_time = datetime.now()

        # Generate two IDs
        id1 = auth._generate_session_id(username, login_time)
        time.sleep(0.01)  # Small delay to ensure different timestamp
        id2 = auth._generate_session_id(username, login_time)

        # IDs should be different (includes timestamp in hash)
        assert id1 != id2
        # IDs should be 16 characters
        assert len(id1) == 16
        assert len(id2) == 16

    def test_get_current_user(self):
        """Test getting current user info."""
        mock_session_state = {
            "username": "test_user",
            "auth_method": "dev",
            "login_time": datetime.now(),
            "session_id": "abc123",
        }

        with patch("apps.web_console.auth.st.session_state", mock_session_state):
            user_info = auth.get_current_user()

        assert user_info["username"] == "test_user"
        assert user_info["auth_method"] == "dev"
        assert user_info["session_id"] == "abc123"
        assert user_info["login_time"] is not None

    def test_logout_clears_session(self):
        """Test logout clears session state."""
        mock_session_state = {
            "authenticated": True,
            "username": "test_user",
            "session_id": "abc123",
        }

        with patch("apps.web_console.auth.st.session_state", mock_session_state):
            with patch("apps.web_console.auth.st.rerun") as mock_rerun:
                auth.logout()

        # Session should be cleared
        assert len(mock_session_state) == 0
        # Should trigger rerun
        mock_rerun.assert_called_once()


class TestAuditLogging:
    """Test audit logging functions."""

    def test_audit_successful_login(self, capsys):
        """Test successful login audit."""
        username = "test_user"
        auth_method = "dev"

        with patch("apps.web_console.auth.st.session_state", {"session_id": "test123"}):
            auth._audit_successful_login(username, auth_method)

        # Check console output (will be fallback since DB not available in tests)
        captured = capsys.readouterr()
        assert "[AUDIT" in captured.out, "Expected [AUDIT marker in output"
        assert username in captured.out, f"Expected username {username} in output"
        assert "login_success" in captured.out, "Expected login_success action in output"

    def test_audit_failed_login(self, capsys):
        """Test failed login audit."""
        auth_method = "dev"

        auth._audit_failed_login(auth_method)

        # Check console output (will be fallback since DB not available in tests)
        captured = capsys.readouterr()
        assert "[AUDIT" in captured.out, "Expected [AUDIT marker in output"
        assert "<failed_login_attempt>" in captured.out, "Expected <failed_login_attempt> in output"
        assert "login_failed" in captured.out, "Expected login_failed action in output"


class TestAuthTypes:
    """Test different authentication types."""

    def test_oauth2_not_implemented(self):
        """Test OAuth2 shows not implemented message."""
        with patch("apps.web_console.auth.AUTH_TYPE", "oauth2"):
            with patch("apps.web_console.auth.st.error") as mock_error:
                with patch("apps.web_console.auth.st.info"):
                    result = auth._oauth2_auth()

        assert result is False
        mock_error.assert_called_once()

    def test_basic_auth_fallback(self):
        """Test basic auth falls back to dev auth for MVP."""
        with patch("apps.web_console.auth.AUTH_TYPE", "basic"):
            with patch("apps.web_console.auth._dev_auth") as mock_dev_auth:
                with patch("apps.web_console.auth.st.warning"):
                    auth._basic_auth()

        # Should call dev auth
        mock_dev_auth.assert_called_once()


class TestDevAuthWorkflow:
    """Test _dev_auth workflow including rate limiting."""

    def test_dev_auth_successful_login(self):
        """Test successful login in dev mode."""
        mock_session_state = {
            "failed_login_attempts": 0,
            "lockout_until": None,
        }

        with patch("apps.web_console.auth.st.session_state", mock_session_state):
            with patch("apps.web_console.auth.st.title"):
                with patch("apps.web_console.auth.st.warning"):
                    with patch("apps.web_console.auth.st.form") as mock_form:
                        with patch("apps.web_console.auth._init_session"):
                            with patch("apps.web_console.auth.st.success"):
                                with patch("apps.web_console.auth.time"):
                                    with patch("apps.web_console.auth.st.rerun"):
                                        # Mock form inputs
                                        mock_form_context = MagicMock()
                                        mock_form.return_value.__enter__.return_value = mock_form_context
                                        mock_form_context.text_input.side_effect = ["admin", "admin"]
                                        mock_form_context.form_submit_button.return_value = True

                                        result = auth._dev_auth()

                                        # Should reset failed attempts on success
                                        assert mock_session_state["failed_login_attempts"] == 0
                                        assert mock_session_state["lockout_until"] is None

    def test_dev_auth_rate_limiting_3_attempts(self):
        """Test rate limiting after 3 failed attempts (30s lockout)."""
        mock_session_state: dict[str, Any] = {
            "failed_login_attempts": 2,  # Start at 2, next failure will be 3rd
            "lockout_until": None,
        }

        with patch("apps.web_console.auth.st") as mock_st:
            # Configure mock
            mock_st.session_state = mock_session_state
            mock_st.form.return_value.__enter__.return_value = None
            mock_st.text_input.side_effect = ["wrong_user", "wrong_pass"]
            mock_st.form_submit_button.return_value = True

            # Call _dev_auth - should trigger 3rd failed attempt
            result = auth._dev_auth()

            # Should fail authentication
            assert result is False

            # Verify lockout is set for 30 seconds (3 attempts)
            assert mock_session_state["failed_login_attempts"] == 3
            assert mock_session_state["lockout_until"] is not None
            lockout_delta = mock_session_state["lockout_until"] - datetime.now()
            # Should be ~30 seconds (allow 5s variance)
            assert 25 <= lockout_delta.total_seconds() <= 35

    def test_dev_auth_rate_limiting_5_attempts(self):
        """Test rate limiting after 5 failed attempts (5min lockout)."""
        mock_session_state: dict[str, Any] = {
            "failed_login_attempts": 4,  # Start at 4, next failure will be 5th
            "lockout_until": None,
        }

        with patch("apps.web_console.auth.st") as mock_st:
            # Configure mock
            mock_st.session_state = mock_session_state
            mock_st.form.return_value.__enter__.return_value = None
            mock_st.text_input.side_effect = ["wrong_user", "wrong_pass"]
            mock_st.form_submit_button.return_value = True

            # Call _dev_auth - should trigger 5th failed attempt
            result = auth._dev_auth()

            # Should fail authentication
            assert result is False

            # Verify lockout is set for 5 minutes (5 attempts = 300 seconds)
            assert mock_session_state["failed_login_attempts"] == 5
            assert mock_session_state["lockout_until"] is not None
            lockout_delta = mock_session_state["lockout_until"] - datetime.now()
            # Should be ~300 seconds (allow 10s variance)
            assert 290 <= lockout_delta.total_seconds() <= 310

    def test_dev_auth_rate_limiting_7_attempts(self):
        """Test rate limiting after 7+ failed attempts (15min lockout)."""
        mock_session_state: dict[str, Any] = {
            "failed_login_attempts": 6,  # Start at 6, next failure will be 7th
            "lockout_until": None,
        }

        with patch("apps.web_console.auth.st") as mock_st:
            # Configure mock
            mock_st.session_state = mock_session_state
            mock_st.form.return_value.__enter__.return_value = None
            mock_st.text_input.side_effect = ["wrong_user", "wrong_pass"]
            mock_st.form_submit_button.return_value = True

            # Call _dev_auth - should trigger 7th failed attempt
            result = auth._dev_auth()

            # Should fail authentication
            assert result is False

            # Verify lockout is set for 15 minutes (7+ attempts = 900 seconds)
            assert mock_session_state["failed_login_attempts"] == 7
            assert mock_session_state["lockout_until"] is not None
            lockout_delta = mock_session_state["lockout_until"] - datetime.now()
            # Should be ~900 seconds (allow 10s variance)
            assert 890 <= lockout_delta.total_seconds() <= 910

    def test_dev_auth_lockout_active(self):
        """Test that active lockout prevents login."""
        future_time = datetime.now() + timedelta(minutes=5)
        mock_session_state = {
            "failed_login_attempts": 5,
            "lockout_until": future_time,
        }

        with patch("apps.web_console.auth.st.session_state", mock_session_state):
            with patch("apps.web_console.auth.st.title"):
                with patch("apps.web_console.auth.st.error") as mock_error:
                    result = auth._dev_auth()

                    # Should show lockout message
                    mock_error.assert_called()
                    error_msg = mock_error.call_args[0][0]
                    assert "locked" in error_msg.lower()
                    assert result is False

    def test_dev_auth_lockout_expired(self):
        """Test that expired lockout resets attempts."""
        past_time = datetime.now() - timedelta(minutes=5)
        mock_session_state = {
            "failed_login_attempts": 5,
            "lockout_until": past_time,
        }

        with patch("apps.web_console.auth.st.session_state", mock_session_state):
            with patch("apps.web_console.auth.st.title"):
                with patch("apps.web_console.auth.st.warning"):
                    with patch("apps.web_console.auth.st.form") as mock_form:
                        # Mock form not submitted (just rendering)
                        mock_form_context = MagicMock()
                        mock_form.return_value.__enter__.return_value = mock_form_context
                        mock_form_context.text_input.side_effect = ["", ""]
                        mock_form_context.form_submit_button.return_value = False

                        result = auth._dev_auth()

                        # Should reset lockout
                        assert mock_session_state["failed_login_attempts"] == 0
                        assert mock_session_state["lockout_until"] is None
                        assert result is False  # Not logged in (form not submitted)
