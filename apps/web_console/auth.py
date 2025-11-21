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
- Failed login rate limiting (3 attempts = 30s lockout, 5 = 5min, 7+ = 15min)
- Constant-time password comparison (prevents timing attacks)

Note:
    OAuth2 implementation is a placeholder for future implementation.
    For P2T3 MVP, we use dev mode with basic auth.
"""

import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timedelta
from typing import Any

import streamlit as st

logger = logging.getLogger(__name__)

from apps.web_console import config
from apps.web_console.config import (
    AUTH_TYPE,
    DATABASE_CONNECT_TIMEOUT,
    DATABASE_URL,
    DEV_PASSWORD,
    DEV_USER,
    SESSION_ABSOLUTE_TIMEOUT_HOURS,
    SESSION_TIMEOUT_MINUTES,
    TRUSTED_PROXY_IPS,
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
    # Check if already logged in (dict-style access for test compatibility)
    if "authenticated" in st.session_state and st.session_state.get("authenticated", False):
        if _check_session_timeout():
            return True
        else:
            # Session expired
            st.session_state.clear()
            st.rerun()

    # Initialize rate limiting state
    if "failed_login_attempts" not in st.session_state:
        st.session_state["failed_login_attempts"] = 0
        st.session_state["lockout_until"] = None

    # Check if locked out
    lockout_until = st.session_state.get("lockout_until")
    if lockout_until:
        if datetime.now() < lockout_until:
            remaining = (lockout_until - datetime.now()).seconds
            st.title("Trading Platform - Login")
            st.error(
                f"🔒 Account temporarily locked due to failed login attempts.\n\n"
                f"Please wait {remaining} seconds before trying again."
            )
            return False
        else:
            # Lockout expired - clear lockout but keep attempt counter for escalation
            # Counter only resets on successful login (line 119)
            st.session_state["lockout_until"] = None

    # Show login form
    st.title("Trading Platform - Login")
    st.warning("Development mode - for local use only")

    with st.form("login_form"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submit = st.form_submit_button("Login")

        if submit:
            # Use constant-time comparison to prevent timing attacks
            username_match = hmac.compare_digest(username, DEV_USER)
            password_match = hmac.compare_digest(password, DEV_PASSWORD)

            if username_match and password_match:
                # Reset failed attempts on successful login
                st.session_state["failed_login_attempts"] = 0
                st.session_state["lockout_until"] = None
                _init_session(username, "dev")
                st.toast("Logged in successfully!")
                st.rerun()
            else:
                # Increment failed attempts
                st.session_state["failed_login_attempts"] += 1
                attempts = st.session_state["failed_login_attempts"]

                # Exponential backoff: 3 attempts = 30s, 5 attempts = 5min, 7+ = 15min
                if attempts >= config.RATE_LIMIT_THRESHOLD_3:
                    lockout_seconds = config.RATE_LIMIT_LOCKOUT_3
                elif attempts >= config.RATE_LIMIT_THRESHOLD_2:
                    lockout_seconds = config.RATE_LIMIT_LOCKOUT_2
                elif attempts >= config.RATE_LIMIT_THRESHOLD_1:
                    lockout_seconds = config.RATE_LIMIT_LOCKOUT_1
                else:
                    lockout_seconds = 0

                if lockout_seconds > 0:
                    st.session_state["lockout_until"] = datetime.now() + timedelta(seconds=lockout_seconds)
                    st.error(
                        f"Invalid username or password.\n\n"
                        f"Too many failed attempts ({attempts}). Account locked for {lockout_seconds} seconds."
                    )
                else:
                    st.error(f"Invalid username or password. ({attempts} failed attempt{'s' if attempts > 1 else ''})")

                _audit_failed_login("dev")

    return False


def _basic_auth() -> bool:
    """
    Basic HTTP authentication (form-based, not HTTP Basic Auth header).

    Note: Despite the name, this uses form-based authentication like dev mode.
    For true HTTP Basic Authentication with Authorization header, this would
    need to be implemented using Streamlit's request context.

    For MVP: Uses same implementation as dev mode.
    For production: Consider removing this mode or implementing proper HTTP Basic Auth.

    Returns:
        bool: True if authenticated
    """
    st.warning("Basic auth mode - testing only, not for production")
    # MVP: Same implementation as dev (form-based login)
    # TODO: Implement proper HTTP Basic Auth or remove this mode
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
    st.session_state["authenticated"] = True
    st.session_state["username"] = username
    st.session_state["auth_method"] = auth_method
    st.session_state["login_time"] = now
    st.session_state["last_activity"] = now
    st.session_state["session_id"] = _generate_session_id(username, now)

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
    login_time = st.session_state.get("login_time")
    if login_time:
        session_age = now - login_time
        if session_age > timedelta(hours=SESSION_ABSOLUTE_TIMEOUT_HOURS):
            st.warning(
                f"Session expired after {SESSION_ABSOLUTE_TIMEOUT_HOURS} hours. Please log in again."
            )
            return False

    # Check idle timeout
    last_activity = st.session_state.get("last_activity")
    if last_activity:
        idle_time = now - last_activity
        if idle_time > timedelta(minutes=SESSION_TIMEOUT_MINUTES):
            st.warning(
                f"Session timed out after {SESSION_TIMEOUT_MINUTES} minutes of inactivity. Please log in again."
            )
            return False

    # Update last activity
    st.session_state["last_activity"] = now
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


def _get_client_ip() -> str:
    """
    Get client IP address from Streamlit context.

    Security Note:
        X-Forwarded-For can be trivially spoofed if not behind a trusted proxy.
        This function only trusts X-Forwarded-For when TRUSTED_PROXY_IPS is configured.

    Behavior:
        - If TRUSTED_PROXY_IPS not set: Returns "localhost" (safe default for dev)
        - If TRUSTED_PROXY_IPS set: Attempts to extract X-Forwarded-For from request headers
        - Falls back to "localhost" if header extraction fails

    MVP Limitation:
        Streamlit does not expose request headers directly in a simple way.
        Current implementation always returns "localhost" regardless of TRUSTED_PROXY_IPS.
        For production deployment with reverse proxy (Nginx), implement proper header
        extraction using streamlit.web.server.server_util or middleware.
        See: https://discuss.streamlit.io/t/how-to-extract-headers-in-streamlit-app/32157

    Returns:
        str: Client IP address from X-Forwarded-For (if trusted) or "localhost"
    """
    # If no trusted proxies configured, return localhost (safe default for dev/MVP)
    if not TRUSTED_PROXY_IPS:
        return "localhost"

    # Try to get X-Forwarded-For header from Streamlit request context
    # NOTE: Streamlit doesn't expose request headers in a stable/documented way
    # For MVP, we return "localhost" as safe default
    # For production with reverse proxy, implement using:
    # 1. streamlit.web.server.server_util.get_request_headers() (if available)
    # 2. Custom middleware to inject headers into session_state
    # 3. Environment variable set by reverse proxy
    try:
        # Attempt to access request context (Streamlit internal API - unstable)
        # This is a placeholder for future implementation
        # from streamlit.web.server import Server
        # headers = Server.get_current().get_request_headers()
        # if headers and "X-Forwarded-For" in headers:
        #     return headers["X-Forwarded-For"].split(",")[0].strip()
        return "localhost"  # MVP: Always localhost (documented limitation)
    except Exception:
        return "localhost"


def audit_to_database(
    user_id: str,
    action: str,
    details: dict[str, Any],
    reason: str | None = None,
    session_id: str | None = None,
) -> None:
    """
    Write audit entry to database.

    Uses non-blocking approach with low timeout to prevent blocking
    authentication flows.

    Connection Pooling Note:
        MVP implementation creates a new connection per audit log entry.
        For production, consider implementing connection pooling using:
        - psycopg.pool.ConnectionPool for sync operations
        - psycopg_pool.AsyncConnectionPool for async operations
        This will reduce connection overhead and improve performance under load.

    Args:
        user_id: Username or identifier
        action: Action type (e.g., "login_success", "login_failed")
        details: Action-specific details
        reason: Optional reason/justification
        session_id: Optional session ID
    """
    ip_address = _get_client_ip()
    audit_entry = {
        "user_id": user_id,
        "action": action,
        "details": details,
        "reason": reason,
        "ip_address": ip_address,
        "session_id": session_id or "N/A",
    }

    try:
        import psycopg
        from psycopg.types.json import Jsonb

        # Set short connection timeout to prevent blocking auth flows
        # Use conninfo parameter instead of URL manipulation to preserve existing query params
        with psycopg.connect(DATABASE_URL, connect_timeout=DATABASE_CONNECT_TIMEOUT) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audit_log (user_id, action, details, reason, ip_address, session_id)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (
                        user_id,
                        action,
                        Jsonb(details),  # Explicitly wrap dict as JSONB for psycopg3
                        reason,
                        ip_address,
                        session_id or "N/A",
                    ),
                )
                conn.commit()
        logger.info("[AUDIT] %s", json.dumps(audit_entry))
    except ModuleNotFoundError:
        # psycopg not installed - fallback to console logging only (never block auth flows)
        logger.error("[AUDIT ERROR] psycopg module not found - using console fallback")
        logger.info("[AUDIT FALLBACK] %s", json.dumps(audit_entry))
    except psycopg.Error as e:
        # Database connection or query error - never block auth flows
        logger.error("[AUDIT ERROR] Database error: %s", e)
        logger.info("[AUDIT FALLBACK] %s", json.dumps(audit_entry))


def _audit_successful_login(username: str, auth_method: str) -> None:
    """
    Audit successful login attempt.

    Args:
        username: Username that logged in
        auth_method: Authentication method used
    """
    details = {
        "auth_method": auth_method,
        "timestamp": datetime.now().isoformat(),
    }
    session_id = st.session_state.get("session_id", "unknown")
    audit_to_database(
        user_id=username,
        action="login_success",
        details=details,
        session_id=session_id,
    )


def _audit_failed_login(auth_method: str) -> None:
    """
    Audit failed login attempt.

    Args:
        auth_method: Authentication method attempted
    """
    details = {
        "auth_method": auth_method,
        "timestamp": datetime.now().isoformat(),
    }
    audit_to_database(
        user_id="<failed_login_attempt>",
        action="login_failed",
        details=details,
        session_id="N/A",  # No session for failed login
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
    session_id = st.session_state.get("session_id", "unknown")

    details = {
        "timestamp": datetime.now().isoformat(),
    }
    audit_to_database(
        user_id=username,
        action="logout",
        details=details,
        session_id=session_id,
    )

    st.session_state.clear()
    st.rerun()
