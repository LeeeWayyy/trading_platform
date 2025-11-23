"""
Authentication Module for Web Console.

Supports four authentication modes:
1. dev: Basic auth for local development (username/password from config)
2. basic: Basic HTTP auth (for testing only - not production-ready)
3. mtls: Mutual TLS with JWT-DN binding (production-ready for P2T3 Phase 2)
4. oauth2: OAuth2/OIDC integration (future implementation)

Security Features:
- Session timeout (15 min idle, 4 hour absolute)
- Audit logging for all auth attempts
- IP address tracking
- Failed login rate limiting (3 attempts = 30s lockout, 5 = 5min, 7+ = 15min)
- Constant-time password comparison (prevents timing attacks)
- mTLS: JWT-DN binding contract (JWT.sub == client DN, enforced on every request)
- mTLS: Header spoofing prevention (X-SSL-Client-Verify validation)

Note:
    OAuth2 implementation is a placeholder for future implementation.
    For P2T3 Phase 2, we add mtls mode with certificate-based authentication.
"""

import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from datetime import datetime, timedelta
from typing import Any

import jwt
import redis
import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx

from apps.web_console.config import (
    AUTH_TYPE,
    DATABASE_CONNECT_TIMEOUT,
    DATABASE_URL,
    DEV_PASSWORD,
    DEV_USER,
    RATE_LIMIT_LOCKOUT_1,
    RATE_LIMIT_LOCKOUT_2,
    RATE_LIMIT_LOCKOUT_3,
    RATE_LIMIT_THRESHOLD_1,
    RATE_LIMIT_THRESHOLD_2,
    RATE_LIMIT_THRESHOLD_3,
    SESSION_ABSOLUTE_TIMEOUT_HOURS,
    SESSION_TIMEOUT_MINUTES,
    TRUSTED_PROXY_IPS,
)
from libs.web_console_auth.config import AuthConfig
from libs.web_console_auth.exceptions import InvalidTokenError, TokenExpiredError, TokenRevokedError
from libs.web_console_auth.jwt_manager import JWTManager

logger = logging.getLogger(__name__)

# Initialize JWTManager for mTLS mode (Component 2 RS256 infrastructure)
# Lazily initialized on first mTLS authentication to avoid unnecessary Redis connections
_jwt_manager: JWTManager | None = None
_redis_client: redis.Redis | None = None


def _get_jwt_manager() -> JWTManager:
    """
    Get or initialize JWTManager singleton.

    Returns:
        JWTManager: Configured JWTManager instance with Redis

    Raises:
        RuntimeError: If initialization fails
    """
    global _jwt_manager, _redis_client

    if _jwt_manager is None:
        try:
            # Initialize Redis client
            # IMPORTANT: decode_responses=False to match SessionManager expectations (bytes keys/values)
            redis_host = os.environ.get("REDIS_HOST", "localhost")
            redis_port = int(os.environ.get("REDIS_PORT", "6379"))
            _redis_client = redis.Redis(
                host=redis_host, port=redis_port, decode_responses=False, socket_timeout=5
            )
            # Test connection
            _redis_client.ping()

            # Load auth config and initialize JWTManager
            auth_config = AuthConfig.from_env()
            _jwt_manager = JWTManager(config=auth_config, redis_client=_redis_client)

            logger.info(
                f"JWTManager initialized with RS256 (Redis: {redis_host}:{redis_port}, "
                f"access_ttl={auth_config.access_token_ttl}s, refresh_ttl={auth_config.refresh_token_ttl}s)"
            )
        except Exception as e:
            logger.error(f"Failed to initialize JWTManager: {e}")
            raise RuntimeError(f"JWTManager initialization failed: {e}") from e

    return _jwt_manager


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
    elif AUTH_TYPE == "mtls":
        return _mtls_auth()
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

            # Use bitwise & to ensure both comparisons always execute (prevent timing side-channel)
            if username_match & password_match:
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
                if attempts >= RATE_LIMIT_THRESHOLD_3:
                    lockout_seconds = RATE_LIMIT_LOCKOUT_3
                elif attempts >= RATE_LIMIT_THRESHOLD_2:
                    lockout_seconds = RATE_LIMIT_LOCKOUT_2
                elif attempts >= RATE_LIMIT_THRESHOLD_1:
                    lockout_seconds = RATE_LIMIT_LOCKOUT_1
                else:
                    lockout_seconds = 0

                if lockout_seconds > 0:
                    st.session_state["lockout_until"] = datetime.now() + timedelta(
                        seconds=lockout_seconds
                    )
                    st.error(
                        f"Invalid username or password.\n\n"
                        f"Too many failed attempts ({attempts}). Account locked for {lockout_seconds} seconds."
                    )
                else:
                    st.error(
                        f"Invalid username or password. ({attempts} failed attempt{'s' if attempts > 1 else ''})"
                    )

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


def _mtls_auth() -> bool:
    """
    Mutual TLS (mTLS) authentication with JWT-DN binding.

    Security Features:
    - Client certificate validation (performed by nginx)
    - JWT-DN binding contract: JWT.sub MUST equal client certificate DN
    - Header spoofing prevention: Validates X-SSL-Client-Verify header
    - Automatic JWT issuance on first successful mTLS verification
    - JWT validation on every request (prevents session hijacking)

    Header Access:
    - Uses Streamlit's session_info API to access nginx-forwarded headers
    - Fallback to environment variables if session_info unavailable

    Returns:
        bool: True if authenticated and JWT-DN binding valid
    """
    # Get request headers from Streamlit context
    headers = _get_request_headers()

    # Step 0: Verify request comes from trusted proxy (defense-in-depth)
    # This prevents X-SSL-Client-* header spoofing if Streamlit is directly reachable
    # (e.g., misconfigured network exposure or lateral movement within cluster)

    # Fail-closed: mTLS mode REQUIRES TRUSTED_PROXY_IPS to be configured
    # This prevents production misconfiguration where mTLS is enabled but proxy
    # verification is accidentally disabled by empty/missing TRUSTED_PROXY_IPS
    if not TRUSTED_PROXY_IPS:
        # Allow insecure dev mode only if explicitly enabled
        if not os.environ.get("ALLOW_INSECURE_MTLS_DEV", "").lower() == "true":
            st.error(
                "🔒 Configuration Error: mTLS mode requires TRUSTED_PROXY_IPS.\n\n"
                "TRUSTED_PROXY_IPS environment variable is not configured.\n\n"
                "For production: Set TRUSTED_PROXY_IPS to your nginx proxy IP.\n"
                "For development: Set ALLOW_INSECURE_MTLS_DEV=true (insecure!)."
            )
            logger.error(
                "mTLS auth rejected: TRUSTED_PROXY_IPS not configured. "
                "This is required for production mTLS to prevent header spoofing. "
                "Set ALLOW_INSECURE_MTLS_DEV=true to allow dev mode (insecure)."
            )
            _audit_failed_login("mtls")
            return False
        else:
            logger.warning(
                "mTLS auth running in INSECURE dev mode (ALLOW_INSECURE_MTLS_DEV=true). "
                "Headers are NOT verified. DO NOT use in production!"
            )
    else:
        # TRUSTED_PROXY_IPS configured - verify request source
        remote_addr = _get_remote_addr()
        if remote_addr not in TRUSTED_PROXY_IPS:
            st.error(
                "🔒 Authentication failed: Request not from trusted proxy.\n\n"
                f"Source: {remote_addr}\n\n"
                "This may indicate a security issue. Please contact your administrator."
            )
            logger.error(
                f"mTLS auth rejected: Request from untrusted source {remote_addr} "
                f"(not in TRUSTED_PROXY_IPS). Possible header spoofing attempt."
            )
            _audit_failed_login("mtls")
            return False

    # Step 1: Verify nginx performed successful mTLS verification
    client_verify = headers.get("X-SSL-Client-Verify", "")
    if client_verify != "SUCCESS":
        st.error(
            "🔒 Client certificate verification failed.\n\n"
            f"Verification status: {client_verify or 'NONE'}\n\n"
            "Please ensure you have a valid client certificate configured in your browser."
        )
        logger.warning(f"mTLS verification failed: {client_verify}")
        _audit_failed_login("mtls")
        return False

    # Step 2: Extract client certificate DN (Distinguished Name)
    client_dn = headers.get("X-SSL-Client-S-DN", "")
    if not client_dn:
        st.error("🔒 Client certificate DN not found. mTLS configuration error.")
        logger.error("X-SSL-Client-S-DN header missing despite SUCCESS verification")
        _audit_failed_login("mtls")
        return False

    # Step 3: Extract CN from DN (for display purposes)
    client_cn = _extract_cn_from_dn(client_dn)

    # Step 4: Check if already authenticated with valid JWT
    if "authenticated" in st.session_state and st.session_state.get("authenticated", False):
        # Validate JWT-DN binding contract on EVERY request
        if _validate_jwt_dn_binding(headers, st.session_state.get("jwt_token", "")):
            # Check session timeout
            if _check_session_timeout():
                return True
            else:
                # Session expired - clear and re-authenticate
                st.session_state.clear()
                st.rerun()
        else:
            # JWT-DN binding validation failed - force re-authentication
            logger.error(f"JWT-DN binding validation failed for {client_dn}")
            st.session_state.clear()
            st.error("🔒 Session validation failed. Please refresh the page.")
            _audit_failed_login("mtls")
            return False

    # Step 5: First-time authentication - issue JWT with DN binding
    jwt_token, claims = _issue_jwt_for_client_dn(client_dn, client_cn, client_verify)
    if not jwt_token:
        st.error("🔒 Failed to issue authentication token. Please try again.")
        logger.error(f"JWT issuance failed for {client_dn}")
        _audit_failed_login("mtls")
        return False

    # Step 6: Initialize session with JWT and client info
    st.session_state["authenticated"] = True
    st.session_state["username"] = client_cn  # Display name
    st.session_state["client_dn"] = client_dn  # Full DN for JWT binding
    st.session_state["jwt_token"] = jwt_token
    st.session_state["jwt_claims"] = claims
    st.session_state["auth_method"] = "mtls"
    st.session_state["login_time"] = datetime.now()
    st.session_state["last_activity"] = datetime.now()
    # Store both session_id and jti for audit/revocation correlation
    if claims:
        st.session_state["session_id"] = claims.get("session_id", claims.get("jti", "unknown"))
        st.session_state["jti"] = claims.get("jti", "unknown")

    # Audit successful login
    _audit_successful_login(client_cn, "mtls")
    logger.info(f"mTLS authentication successful for {client_cn} (DN: {client_dn})")

    return True


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

    For mTLS mode: Revokes JWT token on timeout.

    Returns:
        bool: True if session is valid, False if expired
    """
    now = datetime.now()

    # Check absolute timeout
    login_time = st.session_state.get("login_time")
    if login_time:
        session_age = now - login_time
        if session_age > timedelta(hours=SESSION_ABSOLUTE_TIMEOUT_HOURS):
            _revoke_token_on_timeout("absolute_timeout")
            st.warning(
                f"Session expired after {SESSION_ABSOLUTE_TIMEOUT_HOURS} hours. Please log in again."
            )
            return False

    # Check idle timeout
    last_activity = st.session_state.get("last_activity")
    if last_activity:
        idle_time = now - last_activity
        if idle_time > timedelta(minutes=SESSION_TIMEOUT_MINUTES):
            _revoke_token_on_timeout("idle_timeout")
            st.warning(
                f"Session timed out after {SESSION_TIMEOUT_MINUTES} minutes of inactivity. Please log in again."
            )
            return False

    # Update last activity
    st.session_state["last_activity"] = now
    return True


def _revoke_token_on_timeout(timeout_type: str) -> None:
    """
    Revoke JWT token when session times out (mTLS mode only).

    Args:
        timeout_type: Type of timeout ("idle_timeout" or "absolute_timeout")
    """
    auth_method = st.session_state.get("auth_method", "unknown")
    if auth_method != "mtls":
        return

    if not st.session_state.get("jwt_token"):
        return

    try:
        jwt_manager = _get_jwt_manager()
        jwt_claims = st.session_state.get("jwt_claims", {})
        jti = jwt_claims.get("jti")

        if jti:
            # Pass expiration timestamp (not TTL) - JWTManager calculates TTL internally
            exp = jwt_claims.get("exp")
            if not exp:
                logger.error(
                    f"Cannot revoke JWT on {timeout_type}: 'exp' claim missing. jti={jti}, "
                    f"user={st.session_state.get('username', 'unknown')}"
                )
                return

            jwt_manager.revoke_token(jti, exp)
            logger.info(
                f"Revoked JWT on {timeout_type}: jti={jti}, "
                f"user={st.session_state.get('username', 'unknown')}"
            )
    except Exception as e:
        # Log error but don't block timeout handling
        logger.error(f"Failed to revoke JWT on {timeout_type}: {e}")


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


def _get_remote_addr() -> str:
    """
    Get remote address (immediate upstream caller) from Streamlit context.

    This returns the IP of the direct caller (nginx proxy in production,
    or localhost in dev). Used to verify the request comes from a trusted proxy
    before honoring X-Forwarded-For.

    Returns:
        str: Remote address (IP of immediate upstream caller)
    """
    try:
        # Attempt to get remote address from Streamlit session context
        ctx = get_script_run_ctx()
        if ctx and hasattr(ctx, "session_id"):
            from streamlit.runtime import get_instance

            runtime = get_instance()
            if runtime:
                session = getattr(runtime, "get_session_info", lambda x: None)(ctx.session_id)
                if session and hasattr(session, "ws") and hasattr(session.ws, "request"):
                    # Extract remote IP from WebSocket request
                    # This is the immediate caller (nginx container IP in Docker network)
                    remote_ip: str | None = getattr(session.ws.request, "remote_ip", None)
                    if remote_ip:
                        logger.debug(f"Extracted remote_addr: {remote_ip}")
                        return remote_ip

        # Fallback: check X-Real-IP header (set by nginx)
        headers = _get_request_headers()
        x_real_ip = headers.get("X-Real-IP", "")
        if x_real_ip:
            logger.debug(f"Extracted remote_addr from X-Real-IP: {x_real_ip}")
            return x_real_ip

        # Fallback: environment variable for testing
        env_remote = os.environ.get("REMOTE_ADDR", "127.0.0.1")
        logger.debug(f"Using env REMOTE_ADDR: {env_remote}")
        return env_remote

    except Exception as e:
        logger.debug(f"Failed to extract remote_addr: {e}. Using 127.0.0.1")
        return "127.0.0.1"


def _get_client_ip() -> str:
    """
    Get client IP address from Streamlit context.

    Security Note:
        X-Forwarded-For can be trivially spoofed if not behind a trusted proxy.
        This function only trusts X-Forwarded-For when:
        1. TRUSTED_PROXY_IPS is configured, AND
        2. The immediate upstream caller (remote_addr) is in TRUSTED_PROXY_IPS

        This prevents spoofing if the Streamlit container is directly reachable
        (e.g., misconfigured network exposure or lateral movement).

    Behavior:
        - If TRUSTED_PROXY_IPS not set: Returns "localhost" (safe default for dev)
        - If TRUSTED_PROXY_IPS set: Verifies remote_addr is trusted before using XFF
        - Falls back to "localhost" if verification fails or header extraction fails

    Returns:
        str: Client IP address from X-Forwarded-For (if from trusted proxy) or "localhost"
    """
    # If no trusted proxies configured, return localhost (safe default for dev)
    if not TRUSTED_PROXY_IPS:
        return "localhost"

    # Extract X-Forwarded-For header from nginx
    try:
        # Get remote_addr (immediate upstream caller) to verify it's a trusted proxy
        remote_addr = _get_remote_addr()

        # Defense-in-depth: Only trust X-Forwarded-For if request came from trusted proxy
        # This prevents spoofing if Streamlit container is directly reachable
        if remote_addr not in TRUSTED_PROXY_IPS:
            logger.warning(
                f"Request from untrusted proxy {remote_addr} (not in TRUSTED_PROXY_IPS). "
                "Ignoring X-Forwarded-For to prevent IP spoofing. Using localhost."
            )
            return "localhost"

        headers = _get_request_headers()
        xff = headers.get("X-Forwarded-For", "")
        if xff:
            # X-Forwarded-For format: "client, proxy1, proxy2"
            # Take first (leftmost) IP as client IP
            client_ip = xff.split(",")[0].strip()
            if client_ip:
                logger.debug(
                    f"Extracted client IP from X-Forwarded-For: {client_ip} "
                    f"(verified from trusted proxy {remote_addr})"
                )
                return client_ip

        # Fallback: check environment variable (for testing)
        env_xff = os.environ.get("X_FORWARDED_FOR", "")
        if env_xff:
            client_ip = env_xff.split(",")[0].strip()
            logger.debug(f"Extracted client IP from env X_FORWARDED_FOR: {client_ip}")
            return client_ip

        # No X-Forwarded-For header - fall back to localhost
        logger.debug("No X-Forwarded-For header found, using localhost")
        return "localhost"
    except Exception as e:
        logger.warning(f"Failed to extract client IP: {e}. Using localhost.")
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

        # Set short connection timeout to prevent blocking auth flows
        # Use conninfo parameter instead of URL manipulation to preserve existing query params
        # MVP: New connection per audit entry. Production TODO: Use connection pool
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
                        details,  # psycopg3 auto-adapts dict to JSONB, no Jsonb() wrapper needed
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


def _get_request_headers() -> dict[str, str]:
    """
    Get request headers from Streamlit context.

    Attempts to access nginx-forwarded headers using Streamlit's session_info API.
    Falls back to environment variables if session_info unavailable (e.g., older Streamlit versions).

    Implementation Notes:
    - Streamlit exposes headers via session_info.ws.request (WebSocket request object)
    - This is the recommended approach for production deployment behind nginx
    - Fallback to os.environ for testing/development without nginx

    Returns:
        dict: Request headers (X-SSL-Client-* headers from nginx)
    """
    headers: dict[str, str] = {}

    try:
        # Attempt to get headers from Streamlit session context
        # This works when running behind nginx reverse proxy
        ctx = get_script_run_ctx()
        if ctx and hasattr(ctx, "session_id"):
            # Access session_info to get request headers
            # Note: This API is internal and may change in future Streamlit versions
            from streamlit.runtime import get_instance

            runtime = get_instance()
            if runtime:
                # Try to get session info (API varies by Streamlit version)
                session = getattr(runtime, "get_session_info", lambda x: None)(ctx.session_id)
                if session and hasattr(session, "ws") and hasattr(session.ws, "request"):
                    # Extract headers from WebSocket request
                    ws_headers = session.ws.request.headers
                    # Convert to dict (case-insensitive lookup)
                    for key, value in ws_headers.items():
                        headers[key] = value
                    logger.debug(f"Retrieved {len(headers)} headers from session_info")
    except Exception as e:
        logger.debug(
            f"Could not access session_info headers: {e}. Falling back to environment variables."
        )

    # Fallback: Check environment variables (for testing/development)
    if not headers:
        headers = {
            "X-SSL-Client-Verify": os.environ.get("X_SSL_CLIENT_VERIFY", ""),
            "X-SSL-Client-S-DN": os.environ.get("X_SSL_CLIENT_S_DN", ""),
            "X-SSL-Client-I-DN": os.environ.get("X_SSL_CLIENT_I_DN", ""),
            "X-SSL-Client-Serial": os.environ.get("X_SSL_CLIENT_SERIAL", ""),
            "X-SSL-Client-Fingerprint": os.environ.get("X_SSL_CLIENT_FINGERPRINT", ""),
        }
        logger.debug("Using environment variable fallback for headers")

    return headers


def _extract_cn_from_dn(dn: str) -> str:
    """
    Extract Common Name (CN) from Distinguished Name (DN).

    Example DN: "CN=user@example.com,OU=Users,O=Example Corp,C=US"
    Returns: "user@example.com"

    Args:
        dn: Full Distinguished Name from client certificate

    Returns:
        str: Common Name (CN) component, or full DN if CN not found
    """
    # Parse DN to extract CN (Common Name)
    # DN format: "CN=value,OU=value,O=value,C=value"
    for component in dn.split(","):
        component = component.strip()
        if component.startswith("CN="):
            return component[3:]  # Remove "CN=" prefix

    # Fallback: Return full DN if CN not found
    logger.warning(f"CN not found in DN: {dn}")
    return dn


def _issue_jwt_for_client_dn(
    client_dn: str, client_cn: str, client_verify: str
) -> tuple[str, dict[str, Any]] | tuple[None, None]:
    """
    Issue JWT token with DN binding for authenticated client.

    Uses Component 2's JWTManager for RS256 signing with Redis-backed revocation.

    JWT Claims (via JWTManager):
    - sub: Client DN (CRITICAL: Used for JWT-DN binding contract)
    - typ: "access" (token type)
    - iat: Issued at timestamp
    - exp: Expiration timestamp (from AuthConfig.access_token_ttl)
    - jti: Unique JWT ID (Redis-backed revocation)
    - aud: Audience claim (from AuthConfig)
    - iss: Issuer claim (from AuthConfig)
    - session_binding: Hash of IP + User Agent (session hijacking prevention)

    Args:
        client_dn: Full Distinguished Name from client certificate
        client_cn: Common Name from client certificate
        client_verify: Verification status from nginx (should be "SUCCESS")

    Returns:
        tuple: (JWT token string, claims dict) on success, (None, None) on failure
    """
    try:
        # Get JWTManager instance
        jwt_manager = _get_jwt_manager()

        # Get client info for session binding
        headers = _get_request_headers()
        client_ip = _get_client_ip()
        user_agent = headers.get("User-Agent", "unknown")

        # Fail-closed: If TRUSTED_PROXY_IPS configured but IP extraction failed (localhost),
        # reject token issuance to prevent silently downgrading session binding strength
        if TRUSTED_PROXY_IPS and client_ip == "localhost":
            logger.error(
                f"Token issuance failed for {client_dn}: Cannot determine real client IP "
                "(got localhost). TRUSTED_PROXY_IPS is configured but X-Forwarded-For extraction failed. "
                "This prevents silently downgrading session binding security."
            )
            return None, None

        # Generate session ID (unique per login)
        session_id = secrets.token_urlsafe(16)

        # Use JWTManager to generate RS256 access token
        token = jwt_manager.generate_access_token(
            user_id=client_dn,  # DN as user_id (JWT.sub == client DN for binding)
            session_id=session_id,
            client_ip=client_ip,
            user_agent=user_agent,
        )

        # Decode token (without verification) to get claims for display
        # This is safe because we just generated it and need the claims for session_state
        claims = jwt.decode(token, options={"verify_signature": False})

        # Add custom fields for backward compatibility with existing code
        claims["cn"] = client_cn
        claims["cert_verify"] = client_verify

        logger.info(
            f"Issued RS256 JWT for {client_cn} (DN: {client_dn}, "
            f"jti: {claims.get('jti', 'unknown')}, session: {session_id})"
        )

        return token, claims

    except Exception as e:
        logger.error(f"Failed to issue JWT for {client_dn}: {e}")
        return None, None


def _validate_jwt_dn_binding(headers: dict[str, str], jwt_token: str) -> bool:
    """
    Validate JWT-DN binding contract on EVERY request.

    Uses Component 2's JWTManager for RS256 signature validation with Redis revocation checks.

    Security Contract:
    1. JWT.sub (subject) MUST equal current client DN from X-SSL-Client-S-DN header
    2. X-SSL-Client-Verify MUST be "SUCCESS"
    3. JWT MUST not be expired (with clock skew tolerance)
    4. JWT MUST have valid RS256 signature (public key validation)
    5. JWT MUST not be revoked (Redis JTI blacklist check)
    6. JWT MUST have correct audience and issuer claims
    7. JWT session binding MUST match current IP + User Agent (if strict)

    This prevents:
    - Session hijacking (attacker cannot reuse JWT with different certificate)
    - Token replay attacks (JWT bound to specific client certificate)
    - Header spoofing (validates X-SSL-Client-Verify on every request)
    - Revoked token usage (Redis-backed JTI blacklist)
    - Session fixation (session binding to IP + User Agent)

    Args:
        headers: Current request headers from nginx
        jwt_token: JWT token from session state

    Returns:
        bool: True if JWT-DN binding valid, False otherwise
    """
    try:
        # Verify current mTLS verification is still successful
        current_verify = headers.get("X-SSL-Client-Verify", "")
        if current_verify != "SUCCESS":
            logger.error(f"JWT validation failed: X-SSL-Client-Verify={current_verify}")
            return False

        # Get current client DN from request headers
        current_dn = headers.get("X-SSL-Client-S-DN", "")
        if not current_dn:
            logger.error("JWT validation failed: X-SSL-Client-S-DN header missing")
            return False

        # Get JWTManager instance
        jwt_manager = _get_jwt_manager()

        # Validate JWT using JWTManager (RS256 signature + expiration + revocation + session binding)
        try:
            claims = jwt_manager.validate_token(jwt_token, expected_type="access")
        except TokenExpiredError:
            logger.warning("JWT validation failed: Token expired")
            return False
        except TokenRevokedError:
            logger.error("JWT validation failed: Token revoked (JTI blacklisted)")
            return False
        except InvalidTokenError as e:
            logger.error(f"JWT validation failed: Invalid token: {e}")
            return False

        # Validate JWT-DN binding contract: JWT.sub MUST equal current client DN
        jwt_dn = claims.get("sub", "")
        if jwt_dn != current_dn:
            logger.error(
                f"JWT-DN binding validation failed: JWT.sub={jwt_dn}, current DN={current_dn}"
            )
            return False

        # Validate session binding: IP + User Agent (if strict mode enabled)
        # JWTManager stores session binding in token but doesn't validate it
        # We must validate explicitly to prevent session hijacking
        auth_config = _get_jwt_manager().config
        if auth_config.session_binding_strict:
            # Check IP binding
            token_ip = claims.get("ip", "")
            current_ip = _get_client_ip()

            # Fail closed: Reject if IP is localhost in mTLS mode (indicates extraction failure)
            # Only allow localhost if token was originally issued with localhost
            if current_ip == "localhost" and token_ip != "localhost" and TRUSTED_PROXY_IPS:
                logger.error(
                    f"Session binding failed: Cannot determine real client IP (expected={token_ip}, got=localhost). "
                    "Check X-Forwarded-For header extraction."
                )
                return False

            if token_ip != current_ip:
                logger.error(
                    f"Session binding failed: IP mismatch (token={token_ip}, current={current_ip})"
                )
                return False

            # Check User-Agent binding
            token_ua_hash = claims.get("user_agent_hash", "")
            current_ua = headers.get("User-Agent", "unknown")
            current_ua_hash = hashlib.sha256(current_ua.encode()).hexdigest()
            if token_ua_hash != current_ua_hash:
                logger.error(
                    f"Session binding failed: User-Agent mismatch "
                    f"(token_hash={token_ua_hash}, current_hash={current_ua_hash})"
                )
                return False

        # All checks passed (signature, expiration, revocation, DN binding, session binding)
        return True

    except Exception as e:
        logger.error(f"JWT validation exception: {e}")
        return False


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
    """Logout current user and clear session.

    For mTLS mode: Revokes JWT token to prevent reuse.
    For other modes: Simply clears session state.
    """
    username = st.session_state.get("username", "unknown")
    session_id = st.session_state.get("session_id", "unknown")
    auth_method = st.session_state.get("auth_method", "unknown")

    # Revoke JWT token for mTLS mode (prevents token reuse)
    if auth_method == "mtls" and st.session_state.get("jwt_token"):
        try:
            jwt_manager = _get_jwt_manager()
            jwt_claims = st.session_state.get("jwt_claims", {})
            jti = jwt_claims.get("jti")

            if jti:
                # Pass expiration timestamp (not TTL) - JWTManager calculates TTL internally
                exp = jwt_claims.get("exp")
                if not exp:
                    logger.error(f"Cannot revoke JWT on logout: 'exp' claim missing. jti={jti}, user={username}")
                    return

                jwt_manager.revoke_token(jti, exp)
                logger.info(f"Revoked JWT on logout: jti={jti}, user={username}")
        except Exception as e:
            # Log error but don't block logout
            logger.error(f"Failed to revoke JWT on logout: {e}")

    details = {
        "timestamp": datetime.now().isoformat(),
        "auth_method": auth_method,
    }
    audit_to_database(
        user_id=username,
        action="logout",
        details=details,
        session_id=session_id,
    )

    st.session_state.clear()
    st.rerun()
