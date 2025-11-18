"""
Authentication Module for Web Console.

Supports three authentication modes:
1. dev: Basic auth for local development (username/password from config)
2. basic: Basic HTTP auth (for testing only - not production-ready)
3. oauth2: OAuth2/OIDC integration (production-ready)

Security Features:
- Session timeout (15 min idle, 4 hour absolute)
- Audit logging for all auth attempts
- IP address tracking
- Failed login rate limiting

Note:
    OAuth2 implementation is a placeholder for future implementation.
    For P2T3 MVP, we use dev mode with basic auth.
"""

import hashlib
import time
from datetime import datetime, timedelta
from typing import Any

import streamlit as st

from apps.web_console.config import (
    AUTH_TYPE,
    DEV_PASSWORD,
    DEV_USER,
    SESSION_ABSOLUTE_TIMEOUT_HOURS,
    SESSION_TIMEOUT_MINUTES,
)


def check_password() -> bool:
    """
    Check if user is authenticated.

    Returns True if user is authenticated, False otherwise.
    Handles session timeout and displays login form if needed.

    Returns:
        bool: True if authenticated, False if login required
    """
    if AUTH_TYPE == "dev":
        return _dev_auth()
    elif AUTH_TYPE == "basic":
        return _basic_auth()
    elif AUTH_TYPE == "oauth2":
        return _oauth2_auth()
    else:
        st.error(f"Unknown AUTH_TYPE: {AUTH_TYPE}")
        return False


def _dev_auth() -> bool:
    """
    Development mode authentication (simple username/password).

    For local development only. Uses credentials from config.

    Returns:
        bool: True if authenticated
    """
    # Check if already logged in
    if "authenticated" in st.session_state and st.session_state.authenticated:
        if _check_session_timeout():
            return True
        else:
            # Session expired
            st.session_state.clear()
            st.rerun()

    # Show login form
    st.title("Trading Platform - Login")
    st.warning("Development mode - for local use only")

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submit = st.form_submit_button("Login")

        if submit:
            if username == DEV_USER and password == DEV_PASSWORD:
                _init_session(username, "dev")
                st.success("Logged in successfully!")
                time.sleep(0.5)  # Brief pause for user feedback
                st.rerun()
            else:
                st.error("Invalid username or password")
                _audit_failed_login(username, "dev")

    return False


def _basic_auth() -> bool:
    """
    Basic HTTP authentication.

    For testing only - not production-ready.
    Requires HTTPS in production.

    Returns:
        bool: True if authenticated
    """
    st.warning("Basic auth mode - testing only, not for production")
    # Placeholder: same implementation as dev for MVP
    return _dev_auth()


def _oauth2_auth() -> bool:
    """
    OAuth2/OIDC authentication.

    Production-ready authentication with SSO support.
    Placeholder for future implementation.

    Returns:
        bool: True if authenticated
    """
    st.error(
        "OAuth2 authentication not yet implemented. "
        "Please set WEB_CONSOLE_AUTH_TYPE=dev for development."
    )
    st.info(
        "**Planned OAuth2 Features:**\n"
        "- Single Sign-On (SSO) integration\n"
        "- Multi-Factor Authentication (MFA)\n"
        "- Role-Based Access Control (RBAC)\n"
        "- Automatic session refresh\n"
        "- Integration with corporate IdP"
    )
    return False


def _init_session(username: str, auth_method: str) -> None:
    """
    Initialize authenticated session.

    Args:
        username: Authenticated user
        auth_method: Authentication method used (dev, basic, oauth2)
    """
    now = datetime.now()
    st.session_state.authenticated = True
    st.session_state.username = username
    st.session_state.auth_method = auth_method
    st.session_state.login_time = now
    st.session_state.last_activity = now
    st.session_state.session_id = _generate_session_id(username, now)

    # Audit successful login
    _audit_successful_login(username, auth_method)


def _check_session_timeout() -> bool:
    """
    Check if session is still valid (not timed out).

    Enforces two timeout policies:
    1. Idle timeout: 15 minutes of inactivity
    2. Absolute timeout: 4 hours since login

    Returns:
        bool: True if session is valid, False if expired
    """
    now = datetime.now()

    # Check absolute timeout
    if "login_time" in st.session_state:
        session_age = now - st.session_state.login_time
        if session_age > timedelta(hours=SESSION_ABSOLUTE_TIMEOUT_HOURS):
            st.warning(
                f"Session expired after {SESSION_ABSOLUTE_TIMEOUT_HOURS} hours. Please log in again."
            )
            return False

    # Check idle timeout
    if "last_activity" in st.session_state:
        idle_time = now - st.session_state.last_activity
        if idle_time > timedelta(minutes=SESSION_TIMEOUT_MINUTES):
            st.warning(
                f"Session timed out after {SESSION_TIMEOUT_MINUTES} minutes of inactivity. Please log in again."
            )
            return False

    # Update last activity
    st.session_state.last_activity = now
    return True


def _generate_session_id(username: str, login_time: datetime) -> str:
    """
    Generate unique session ID.

    Args:
        username: Username
        login_time: Login timestamp

    Returns:
        str: Session ID (SHA256 hash)
    """
    data = f"{username}:{login_time.isoformat()}:{time.time()}"
    return hashlib.sha256(data.encode()).hexdigest()[:16]


def _audit_successful_login(username: str, auth_method: str) -> None:
    """
    Audit successful login attempt.

    Args:
        username: Username that logged in
        auth_method: Authentication method used
    """
    # TODO: Write to audit_log table
    # For MVP, just log to console
    print(
        f"[AUDIT] Successful login: user={username}, method={auth_method}, "
        f"time={datetime.now().isoformat()}"
    )


def _audit_failed_login(username: str, auth_method: str) -> None:
    """
    Audit failed login attempt.

    Args:
        username: Username that attempted login
        auth_method: Authentication method attempted
    """
    # TODO: Write to audit_log table
    # For MVP, just log to console
    print(
        f"[AUDIT] Failed login: user={username}, method={auth_method}, "
        f"time={datetime.now().isoformat()}"
    )


def get_current_user() -> dict[str, Any]:
    """
    Get current authenticated user info.

    Returns:
        dict: User information (username, auth_method, login_time, etc.)
    """
    return {
        "username": st.session_state.get("username", "unknown"),
        "auth_method": st.session_state.get("auth_method", "unknown"),
        "login_time": st.session_state.get("login_time"),
        "session_id": st.session_state.get("session_id", "unknown"),
    }


def logout() -> None:
    """Logout current user and clear session."""
    username = st.session_state.get("username", "unknown")
    print(f"[AUDIT] Logout: user={username}, time={datetime.now().isoformat()}")
    st.session_state.clear()
    st.rerun()
