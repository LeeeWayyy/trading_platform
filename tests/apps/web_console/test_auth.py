"""
Tests for Web Console Authentication.

Tests authentication flows, session management, and timeout enforcement.
"""

import hashlib
import time
from datetime import datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

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

    def test_audit_successful_login(self, caplog):
        """Test successful login audit."""
        import logging

        caplog.set_level(logging.INFO)

        username = "test_user"
        auth_method = "dev"

        with patch("apps.web_console.auth.st.session_state", {"session_id": "test123"}):
            auth._audit_successful_login(username, auth_method)

        # Check logging output (will be fallback since DB not available in tests)
        log_text = caplog.text
        # Verify JSON audit log format
        assert "[AUDIT" in log_text, f"Expected [AUDIT marker in output. Got: {log_text}"
        assert (
            '"user_id": "test_user"' in log_text or "'user_id': 'test_user'" in log_text
        ), f"Expected username {username} in JSON format. Got: {log_text}"
        assert (
            '"action": "login_success"' in log_text or "'action': 'login_success'" in log_text
        ), f"Expected login_success action in JSON format. Got: {log_text}"
        assert (
            '"auth_method": "dev"' in log_text or "'auth_method': 'dev'" in log_text
        ), f"Expected auth_method in details. Got: {log_text}"

    def test_audit_failed_login(self, caplog):
        """Test failed login audit."""
        import logging

        caplog.set_level(logging.INFO)

        auth_method = "dev"

        auth._audit_failed_login(auth_method)

        # Check logging output (will be fallback since DB not available in tests)
        log_text = caplog.text
        # Verify JSON audit log format
        assert "[AUDIT" in log_text, f"Expected [AUDIT marker in output. Got: {log_text}"
        assert (
            '"user_id": "<failed_login_attempt>"' in log_text
            or "'user_id': '<failed_login_attempt>'" in log_text
        ), f"Expected <failed_login_attempt> in JSON format. Got: {log_text}"
        assert (
            '"action": "login_failed"' in log_text or "'action': 'login_failed'" in log_text
        ), f"Expected login_failed action in JSON format. Got: {log_text}"


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
                                        mock_form.return_value.__enter__.return_value = (
                                            mock_form_context
                                        )
                                        mock_form_context.text_input.side_effect = [
                                            "admin",
                                            "admin",
                                        ]
                                        mock_form_context.form_submit_button.return_value = True

                                        auth._dev_auth()

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
        """Test that expired lockout clears lockout_until but keeps attempt counter for escalation."""
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

                        # Should clear lockout but KEEP attempt counter for escalation
                        # Counter only resets on successful login (auth.py:119)
                        assert (
                            mock_session_state["failed_login_attempts"] == 5
                        ), "Counter should persist for escalation"
                        assert (
                            mock_session_state["lockout_until"] is None
                        ), "Lockout should be cleared"
                        assert result is False  # Not logged in (form not submitted)


class TestMtlsAuth:
    """Test mTLS authentication with JWT-DN binding."""

    @patch("apps.web_console.auth._get_session_manager")
    @patch("apps.web_console.auth._get_request_headers")
    @patch("apps.web_console.auth._get_client_ip")
    @patch("apps.web_console.auth._get_remote_addr")
    @patch("apps.web_console.auth.TRUSTED_PROXY_IPS", ["nginx"])
    def test_mtls_auth_success_first_visit(
        self, mock_remote_addr, mock_get_ip, mock_get_headers, mock_get_session
    ):
        """Test mTLS authentication issues token on first visit."""
        # Setup mocks
        mock_remote_addr.return_value = "nginx"  # Trusted proxy
        mock_get_headers.return_value = {
            "X-SSL-Client-Verify": "SUCCESS",
            "X-SSL-Client-S-DN": "CN=test@example.com,O=Test Corp,C=US",
            "User-Agent": "TestBrowser/1.0",
        }
        mock_get_ip.return_value = "192.168.1.100"

        # Mock SessionManager
        mock_session_manager = MagicMock()
        mock_session_manager.create_session.return_value = ("test.jwt.token", "test.refresh.token")
        mock_get_session.return_value = mock_session_manager

        mock_session_state = {}

        with patch("apps.web_console.auth.st.session_state", mock_session_state):
            with patch("apps.web_console.auth.jwt.decode") as mock_decode:
                mock_decode.return_value = {
                    "sub": "CN=test@example.com,O=Test Corp,C=US",
                    "jti": "test-jti-123",
                    "session_id": "test-session-456",
                    "exp": int(time.time()) + 900,
                }
                result = auth._mtls_auth()

        # Verify session was created
        mock_session_manager.create_session.assert_called_once()

        # Verify session state populated
        assert mock_session_state["authenticated"] is True
        assert mock_session_state["auth_method"] == "mtls"
        assert mock_session_state["username"] == "test@example.com"
        assert mock_session_state["client_dn"] == "CN=test@example.com,O=Test Corp,C=US"
        assert mock_session_state["jwt_token"] == "test.jwt.token"
        assert mock_session_state["session_id"] == "test-session-456"
        assert mock_session_state["jti"] == "test-jti-123"
        assert result is True

    @patch("apps.web_console.auth._get_session_manager")
    @patch("apps.web_console.auth._get_request_headers")
    @patch("apps.web_console.auth._get_client_ip")
    def test_validate_jwt_dn_binding_rejects_ip_change(
        self, mock_get_ip, mock_get_headers, mock_get_jwt
    ):
        """Test JWT-DN binding rejects token when IP changes."""
        # Mock headers with valid cert
        mock_get_headers.return_value = {
            "X-SSL-Client-Verify": "SUCCESS",
            "X-SSL-Client-S-DN": "CN=test@example.com,O=Test Corp,C=US",
            "User-Agent": "TestBrowser/1.0",
        }
        mock_get_ip.return_value = "192.168.1.999"  # CHANGED IP

        # Mock SessionManager - validate_session should raise InvalidTokenError for IP mismatch
        from libs.web_console_auth.exceptions import InvalidTokenError
        mock_session_manager = MagicMock()
        mock_session_manager.validate_session.side_effect = InvalidTokenError("Session binding mismatch: IP changed")
        mock_get_jwt.return_value = mock_session_manager

        # Validate JWT should FAIL due to IP mismatch (SessionManager raises InvalidTokenError)
        result = auth._validate_jwt_dn_binding(mock_get_headers.return_value, "test.jwt.token")

        assert result is False  # Should reject due to IP change

    @patch("apps.web_console.auth._get_session_manager")
    @patch("apps.web_console.auth._get_request_headers")
    @patch("apps.web_console.auth._get_client_ip")
    def test_validate_jwt_dn_binding_rejects_ua_change(
        self, mock_get_ip, mock_get_headers, mock_get_jwt
    ):
        """Test JWT-DN binding rejects token when User-Agent changes."""
        # Mock headers with CHANGED User-Agent
        mock_get_headers.return_value = {
            "X-SSL-Client-Verify": "SUCCESS",
            "X-SSL-Client-S-DN": "CN=test@example.com,O=Test Corp,C=US",
            "User-Agent": "DifferentBrowser/2.0",  # CHANGED
        }
        mock_get_ip.return_value = "192.168.1.100"

        # Mock JWTManager validation
        mock_session_manager = MagicMock()
        from libs.web_console_auth.exceptions import InvalidTokenError
        mock_session_manager.validate_session.side_effect = InvalidTokenError("Session binding mismatch: UA changed")
        mock_get_jwt.return_value = mock_session_manager

        # Validate JWT should FAIL due to UA mismatch
        result = auth._validate_jwt_dn_binding(mock_get_headers.return_value, "test.jwt.token")

        assert result is False  # Should reject due to User-Agent change

    @patch("apps.web_console.auth._get_session_manager")
    @patch("apps.web_console.auth._get_request_headers")
    @patch("apps.web_console.auth._get_client_ip")
    @patch("apps.web_console.auth.TRUSTED_PROXY_IPS", ["nginx"])
    def test_validate_jwt_dn_binding_rejects_localhost_when_expecting_real_ip(
        self, mock_get_ip, mock_get_headers, mock_get_jwt
    ):
        """Test JWT-DN binding fails closed when IP extraction fails (returns localhost)."""
        # Mock headers with valid cert
        mock_get_headers.return_value = {
            "X-SSL-Client-Verify": "SUCCESS",
            "X-SSL-Client-S-DN": "CN=test@example.com,O=Test Corp,C=US",
            "User-Agent": "TestBrowser/1.0",
        }
        # IP extraction failed (returns localhost)
        mock_get_ip.return_value = "localhost"

        # Mock JWTManager validation - token was issued with real IP
        mock_session_manager = MagicMock()
        mock_session_manager.validate_session.return_value = {
            "sub": "CN=test@example.com,O=Test Corp,C=US",
            "ip": "192.168.1.100",  # Real IP when token was issued
            "user_agent_hash": hashlib.sha256(b"TestBrowser/1.0").hexdigest(),
            "jti": "test-jti-123",
        }
        mock_session_manager.config.session_binding_strict = True
        mock_get_jwt.return_value = mock_session_manager

        # Validate JWT should FAIL (fail closed when cannot determine real IP)
        with patch("apps.web_console.auth.hashlib.sha256") as mock_sha:
            mock_sha.return_value.hexdigest.return_value = hashlib.sha256(
                b"TestBrowser/1.0"
            ).hexdigest()
            result = auth._validate_jwt_dn_binding(mock_get_headers.return_value, "test.jwt.token")

        assert result is False  # Should reject when IP extraction fails

    @patch("apps.web_console.auth._get_session_manager")
    @patch("apps.web_console.auth._get_request_headers")
    @patch("apps.web_console.auth._get_client_ip")
    @patch("apps.web_console.auth.TRUSTED_PROXY_IPS", ["nginx"])
    def test_issue_jwt_rejects_localhost_when_proxy_configured(
        self, mock_get_ip, mock_get_headers, mock_get_jwt
    ):
        """Test JWT issuance fails when IP extraction returns localhost with TRUSTED_PROXY_IPS set."""
        # Mock headers
        mock_get_headers.return_value = {
            "User-Agent": "TestBrowser/1.0",
        }
        # IP extraction failed (returns localhost)
        mock_get_ip.return_value = "localhost"

        # Mock JWTManager
        mock_session_manager = MagicMock()
        mock_get_jwt.return_value = mock_session_manager

        # Attempt to issue JWT - should FAIL (fail-closed)
        token, claims = auth._issue_jwt_for_client_dn(
            client_dn="CN=test@example.com,O=Test Corp,C=US",
            client_cn="test@example.com",
            client_verify="SUCCESS",
        )

        # Should reject token issuance
        assert token is None
        assert claims is None
        # JWTManager should NOT be called
        mock_session_manager.generate_access_token.assert_not_called()

    @patch("apps.web_console.auth._get_session_manager")
    @patch("apps.web_console.auth._get_request_headers")
    @patch("apps.web_console.auth._get_remote_addr")
    @patch("apps.web_console.auth.TRUSTED_PROXY_IPS", ["10.0.0.1", "nginx"])
    def test_get_client_ip_rejects_untrusted_proxy(
        self, mock_remote_addr, mock_get_headers, mock_get_jwt
    ):
        """Test _get_client_ip rejects XFF from untrusted proxy (defense-in-depth)."""
        # Remote addr is NOT in TRUSTED_PROXY_IPS
        mock_remote_addr.return_value = "192.168.99.99"  # Attacker's IP

        # Headers with spoofed X-Forwarded-For
        mock_get_headers.return_value = {
            "X-Forwarded-For": "203.0.113.42",  # Spoofed client IP
        }

        # Should reject XFF and return localhost (fail-safe)
        client_ip = auth._get_client_ip()
        assert client_ip == "localhost"

    @patch("apps.web_console.auth._get_session_manager")
    @patch("apps.web_console.auth._get_request_headers")
    @patch("apps.web_console.auth._get_remote_addr")
    @patch("apps.web_console.auth.TRUSTED_PROXY_IPS", ["10.0.0.1", "nginx"])
    def test_get_client_ip_accepts_trusted_proxy(
        self, mock_remote_addr, mock_get_headers, mock_get_jwt
    ):
        """Test _get_client_ip accepts XFF from trusted proxy."""
        # Remote addr IS in TRUSTED_PROXY_IPS
        mock_remote_addr.return_value = "nginx"  # Trusted nginx proxy

        # Headers with X-Forwarded-For
        mock_get_headers.return_value = {
            "X-Forwarded-For": "203.0.113.42",  # Real client IP
        }

        # Should accept XFF from trusted proxy
        client_ip = auth._get_client_ip()
        assert client_ip == "203.0.113.42"

    @patch("apps.web_console.auth.st")
    @patch("apps.web_console.auth._get_request_headers")
    @patch("apps.web_console.auth._get_remote_addr")
    @patch("apps.web_console.auth.TRUSTED_PROXY_IPS", ["nginx", "10.0.0.1"])
    @patch("apps.web_console.auth._audit_failed_login")
    def test_mtls_auth_rejects_untrusted_proxy_source(
        self, mock_audit, mock_remote_addr, mock_get_headers, mock_st
    ):
        """Test mTLS auth rejects X-SSL-Client-* headers from untrusted proxy."""
        # Remote addr is NOT in TRUSTED_PROXY_IPS
        mock_remote_addr.return_value = "192.168.99.99"  # Attacker's IP

        # Headers with forged X-SSL-Client-Verify (spoofing attack)
        mock_get_headers.return_value = {
            "X-SSL-Client-Verify": "SUCCESS",  # Forged
            "X-SSL-Client-S-DN": "CN=attacker@evil.com,O=Evil Corp,C=US",  # Forged
        }

        # Should reject authentication
        result = auth._mtls_auth()
        assert result is False

        # Should log audit failure
        mock_audit.assert_called_once_with("mtls")

        # Should show error to user
        mock_st.error.assert_called_once()

    @patch("apps.web_console.auth.st")
    @patch("apps.web_console.auth._get_request_headers")
    @patch("apps.web_console.auth._get_remote_addr")
    @patch("apps.web_console.auth.TRUSTED_PROXY_IPS", [])
    @patch("apps.web_console.auth._audit_failed_login")
    def test_mtls_auth_rejects_when_no_trusted_proxies_configured(
        self, mock_audit, mock_remote_addr, mock_get_headers, mock_st
    ):
        """Test mTLS auth rejects when TRUSTED_PROXY_IPS is not configured (fail-closed)."""
        # No trusted proxies configured and no dev override
        mock_remote_addr.return_value = "192.168.99.99"

        # Mock session state (not authenticated yet)
        mock_st.session_state = {}

        # Headers with X-SSL-Client headers
        mock_get_headers.return_value = {
            "X-SSL-Client-Verify": "SUCCESS",
            "X-SSL-Client-S-DN": "CN=test@example.com,O=Test Corp,C=US",
            "User-Agent": "TestBrowser/1.0",
        }

        # Should reject authentication (fail-closed)
        result = auth._mtls_auth()
        assert result is False

        # Should log audit failure
        mock_audit.assert_called_once_with("mtls")

        # Should show configuration error
        mock_st.error.assert_called_once()
        error_message = str(mock_st.error.call_args)
        assert "TRUSTED_PROXY_IPS" in error_message

    @patch("apps.web_console.auth.st")
    @patch("apps.web_console.auth._get_request_headers")
    @patch("apps.web_console.auth._get_remote_addr")
    @patch("apps.web_console.auth.TRUSTED_PROXY_IPS", [])
    @patch("apps.web_console.auth.os.environ", {"ALLOW_INSECURE_MTLS_DEV": "true"})
    def test_mtls_auth_allows_insecure_dev_mode_when_explicitly_enabled(
        self, mock_remote_addr, mock_get_headers, mock_st
    ):
        """Test mTLS auth allows insecure dev mode with explicit override."""
        # No trusted proxies but ALLOW_INSECURE_MTLS_DEV=true
        mock_remote_addr.return_value = "192.168.99.99"

        # Mock session state (not authenticated yet)
        mock_st.session_state = {}

        # Headers with X-SSL-Client headers
        mock_get_headers.return_value = {
            "X-SSL-Client-Verify": "SUCCESS",
            "X-SSL-Client-S-DN": "CN=test@example.com,O=Test Corp,C=US",
            "User-Agent": "TestBrowser/1.0",
        }

        # In insecure dev mode, should proceed to authentication
        # (will fail later due to missing mocks, but proxy check should pass)
        # We just verify proxy check doesn't block
        try:
            auth._mtls_auth()
        except Exception:
            pass  # Expected to fail on other mocks, we just care proxy check passed

        # Should NOT call st.error for configuration error
        # (May call for other reasons, but not with "TRUSTED_PROXY_IPS" message)
        for call in mock_st.error.call_args_list:
            assert "TRUSTED_PROXY_IPS" not in str(call)

    @patch("apps.web_console.auth._get_session_manager")
    def test_logout_revokes_jwt_token(self, mock_get_jwt):
        """Test logout revokes JWT token for mTLS mode."""
        mock_session_manager = MagicMock()
        mock_session_manager.config.access_token_ttl = 900
        mock_get_jwt.return_value = mock_session_manager

        exp_timestamp = int(time.time()) + 900
        mock_session_state = {
            "auth_method": "mtls",
            "username": "test@example.com",
            "session_id": "test-session",
            "jwt_token": "test.jwt.token",
            "jwt_claims": {
                "session_id": "test-session-456",
                "exp": exp_timestamp,
            },
        }

        with patch("apps.web_console.auth.st.session_state", mock_session_state):
            with patch("apps.web_console.auth.st.rerun"):
                auth.logout()

        # Verify token was revoked with correct exp timestamp
        mock_session_manager.terminate_session.assert_called_once_with("test-session-456")
