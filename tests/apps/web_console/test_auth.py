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

        auth._audit_successful_login(username, auth_method)

        # Check console output
        captured = capsys.readouterr()
        assert "[AUDIT] Successful login" in captured.out
        assert f"user={username}" in captured.out
        assert f"method={auth_method}" in captured.out

    def test_audit_failed_login(self, capsys):
        """Test failed login audit."""
        username = "bad_user"
        auth_method = "dev"

        auth._audit_failed_login(username, auth_method)

        # Check console output
        captured = capsys.readouterr()
        assert "[AUDIT] Failed login" in captured.out
        assert f"user={username}" in captured.out
        assert f"method={auth_method}" in captured.out


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
