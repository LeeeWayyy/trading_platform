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
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import jwt
import redis
import streamlit as st
from prometheus_client import Counter, Gauge
from psycopg_pool import ConnectionPool
from streamlit.runtime.scriptrunner import get_script_run_ctx

if TYPE_CHECKING:
    from apps.web_console.auth.idp_health import IdPHealthChecker
    from apps.web_console.auth.mtls_fallback import CertificateInfo, MtlsFallbackValidator

# Import session manager for OAuth2
from apps.web_console.auth.session_manager import (
    get_session_cookie,
    validate_session,
)
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
from libs.web_console_auth.exceptions import (
    InvalidTokenError,
    RateLimitExceededError,
    SessionLimitExceededError,
    TokenExpiredError,
    TokenRevokedError,
)
from libs.web_console_auth.jwt_manager import JWTManager
from libs.web_console_auth.session import SessionManager

logger = logging.getLogger(__name__)

# Allowlist: ONLY these environments can use dev auth (fail-closed security)
_ALLOWED_DEV_AUTH_ENVIRONMENTS = frozenset(
    {
        "development",
        "dev",
        "local",
        "test",
        "ci",
    }
)

# ============================================================================
# Prometheus Metrics (Component 6+7: P2T3 Phase 3)
# ============================================================================
# Enable multiprocess mode for Streamlit
if os.getenv("PROMETHEUS_MULTIPROC_DIR"):
    from prometheus_client import CollectorRegistry, multiprocess

    registry = CollectorRegistry()
    multiprocess.MultiProcessCollector(registry)  # type: ignore[no-untyped-call]
else:
    from prometheus_client import REGISTRY as registry

# Session Management Metrics (3 total - 2 removed after review)
session_created_total = Counter(
    "oauth2_session_created_total",
    "Total sessions created",
    registry=registry,
)

session_signature_failures_total = Counter(
    "oauth2_session_signature_failures_total",
    "Total session signature validation failures",
    ["reason"],
    registry=registry,
)

active_sessions_count = Gauge(
    "oauth2_active_sessions_count",
    "Number of active sessions in Redis",
    registry=registry,
)

# OAuth2 Flow Metrics (4 total)
authorization_total = Counter(
    "oauth2_authorization_total",
    "Total OAuth2 authorization attempts",
    ["result"],
    registry=registry,
)

authorization_failures_total = Counter(
    "oauth2_authorization_failures_total",
    "Total OAuth2 authorization failures by reason",
    ["reason"],
    registry=registry,
)

# NOTE: Token refresh metrics are placeholders for FastAPI auth service
# Streamlit web_console does NOT handle token refresh (only FastAPI /auth/refresh endpoint does)
# These metrics are defined here for alert compatibility but will NOT emit values from Streamlit
token_refresh_total = Counter(
    "oauth2_token_refresh_total",
    "Total OAuth2 token refresh attempts (FastAPI only, not Streamlit)",
    ["result"],
    registry=registry,
)

token_refresh_failures_total = Counter(
    "oauth2_token_refresh_failures_total",
    "Total OAuth2 token refresh failures by reason (FastAPI only, not Streamlit)",
    ["reason"],
    registry=registry,
)

# Initialize SessionManager for mTLS mode (Component 2 RS256 + session management)
# Lazily initialized on first mTLS authentication to avoid unnecessary Redis connections
_session_manager: SessionManager | None = None
_redis_client: redis.Redis | None = None

# Throttling for active_sessions_count updates (avoid excessive SCAN operations)
_last_session_count_update: datetime | None = None
_session_count_update_interval = timedelta(seconds=60)  # Update every 60s max


def _update_active_sessions_count() -> None:
    """
    Update active_sessions_count gauge with Redis SCAN (throttled).

    Component 6+7: Prometheus metrics for session monitoring.

    This function is throttled to run at most once per 60 seconds to avoid
    excessive SCAN operations on Redis. Called opportunistically during
    OAuth2 authentication checks.

    Notes:
        - Uses Redis SCAN with pattern matching (web_console:session:*)
        - Counts all session keys (both active and expired, before TTL cleanup)
        - SCAN timeout: 1 second (fail-safe for large key counts)
        - Silently fails on errors (metric becomes stale but doesn't block auth)
    """
    global _last_session_count_update, _redis_client

    # Throttling: Only update every 60 seconds
    now = datetime.now(UTC)
    if _last_session_count_update is not None:
        if now - _last_session_count_update < _session_count_update_interval:
            return  # Too soon, skip update

    try:
        # Ensure Redis client is initialized
        if _redis_client is None:
            redis_host = os.environ.get("REDIS_HOST", "localhost")
            redis_port = int(os.environ.get("REDIS_PORT", "6379"))
            _redis_client = redis.Redis(
                host=redis_host,
                port=redis_port,
                db=1,  # CRITICAL: Sessions DB (must match _validate's redis.asyncio.Redis(..., db=1))
                decode_responses=False,
                socket_timeout=1,  # 1s timeout for SCAN operations
            )

        # Use SCAN to count session keys with pattern matching
        # Pattern: web_console:session:* (matches SessionManager's redis_session_prefix)
        session_count = 0
        cursor = 0
        while True:
            # Sync Redis client - scan returns tuple directly (not awaitable)
            cursor, keys = _redis_client.scan(  # type: ignore[misc]
                cursor=cursor, match="web_console:session:*", count=100
            )
            session_count += len(keys)
            if cursor == 0:
                break  # SCAN complete

        # Update gauge
        active_sessions_count.set(session_count)
        _last_session_count_update = now

        logger.debug(
            f"Updated active_sessions_count: {session_count} sessions",
            extra={"session_count": session_count},
        )

    except Exception as e:
        # Fail silently (don't block authentication)
        logger.warning(
            f"Failed to update active_sessions_count: {e}",
            extra={"error": str(e)},
        )


def _get_session_manager() -> SessionManager:
    """
    Get or initialize SessionManager singleton.

    Returns:
        SessionManager: Configured SessionManager instance with Redis

    Raises:
        RuntimeError: If initialization fails
    """
    global _session_manager, _redis_client

    if _session_manager is None:
        try:
            # Initialize Redis client
            # IMPORTANT: decode_responses=False to match SessionManager expectations (bytes keys/values)
            redis_host = os.environ.get("REDIS_HOST", "localhost")
            redis_port = int(os.environ.get("REDIS_PORT", "6379"))
            _redis_client = redis.Redis(
                host=redis_host,
                port=redis_port,
                db=1,  # CRITICAL: Sessions DB (must match _validate's redis.asyncio.Redis(..., db=1))
                decode_responses=False,
                socket_timeout=5,
            )
            # Test connection
            _redis_client.ping()

            # Load auth config
            auth_config = AuthConfig.from_env()

            # Initialize JWTManager (required by SessionManager)
            # JWTManager signature: JWTManager(config, redis_client)
            jwt_manager = JWTManager(config=auth_config, redis_client=_redis_client)

            # Initialize SessionManager with required dependencies
            # Signature: SessionManager(redis_client, jwt_manager, auth_config)
            _session_manager = SessionManager(
                redis_client=_redis_client, jwt_manager=jwt_manager, auth_config=auth_config
            )

            logger.info(
                f"SessionManager initialized with RS256 (Redis: {redis_host}:{redis_port}, "
                f"access_ttl={auth_config.access_token_ttl}s, refresh_ttl={auth_config.refresh_token_ttl}s, "
                f"max_sessions={auth_config.max_sessions_per_user})"
            )
        except Exception as e:
            logger.error(f"Failed to initialize SessionManager: {e}")
            raise RuntimeError(f"SessionManager initialization failed: {e}") from e

    return _session_manager


# Component 6 singletons (P2T3 Phase 3)
_idp_health_checker: "IdPHealthChecker | None" = None
_mtls_fallback_validator: "MtlsFallbackValidator | None" = None

# Audit log connection pool (lazy init)
_audit_db_pool: ConnectionPool | None = None
# Dev session Redis client (lazy init)
_dev_session_redis: redis.Redis | None = None


def _get_audit_db_pool() -> ConnectionPool:
    """Get or initialize a shared psycopg_pool.ConnectionPool for audit logging."""
    global _audit_db_pool

    if _audit_db_pool is None:
        _audit_db_pool = ConnectionPool(
            conninfo=DATABASE_URL,
            min_size=1,
            max_size=5,
            kwargs={"connect_timeout": int(DATABASE_CONNECT_TIMEOUT)},
        )
        _audit_db_pool.open()
        logger.info(
            "Audit DB pool initialized",
            extra={"pool_min": 1, "pool_max": 5, "connect_timeout": DATABASE_CONNECT_TIMEOUT},
        )

    return _audit_db_pool


def _get_idp_health_checker() -> "IdPHealthChecker":
    """
    Get or initialize IdPHealthChecker singleton with persistent state (Component 6).

    CRITICAL: Persists health check state across Streamlit reruns to prevent
    consecutive failure counters from resetting to 0 on every request.
    Without singleton pattern, fallback mode would never activate.

    Returns:
        IdPHealthChecker: Singleton instance with persistent state

    Raises:
        ValueError: If AUTH0_DOMAIN not configured
    """
    global _idp_health_checker

    if _idp_health_checker is None:
        from apps.web_console.auth.idp_health import IdPHealthChecker

        auth0_domain = os.getenv("AUTH0_DOMAIN", "")
        if not auth0_domain:
            raise ValueError("AUTH0_DOMAIN not configured")

        _idp_health_checker = IdPHealthChecker(auth0_domain=auth0_domain)
        logger.info(f"IdPHealthChecker initialized for domain: {auth0_domain}")

    return _idp_health_checker


def _get_mtls_fallback_validator() -> "MtlsFallbackValidator":
    """
    Get or initialize MtlsFallbackValidator singleton with cached CRL (Component 6).

    CRITICAL: Persists CRL cache across authentication attempts.
    Without singleton pattern, the 1-hour CRL cache would be useless as
    validator would be recreated on every request.

    Returns:
        MtlsFallbackValidator: Singleton instance with persistent CRL cache

    Raises:
        ValueError: If MTLS_ADMIN_CN_ALLOWLIST not configured
    """
    global _mtls_fallback_validator

    if _mtls_fallback_validator is None:
        from apps.web_console.auth.mtls_fallback import (
            MtlsFallbackValidator,
            get_admin_cn_allowlist,
            get_crl_url,
        )

        admin_cn_allowlist = get_admin_cn_allowlist()
        if not admin_cn_allowlist:
            raise ValueError("MTLS_ADMIN_CN_ALLOWLIST not configured")

        crl_url = get_crl_url()
        _mtls_fallback_validator = MtlsFallbackValidator(
            admin_cn_allowlist=admin_cn_allowlist,
            crl_url=crl_url,
        )
        logger.info(f"MtlsFallbackValidator initialized (CRL: {crl_url})")

    return _mtls_fallback_validator


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


def _handle_session_timeout_check() -> bool | None:
    """
    Check session timeout and handle cleanup if expired.

    Returns:
        True if session is valid, None if expired (cleanup handled internally)
    """
    if _check_session_timeout():
        return True
    else:
        # Session expired - clear and prepare for rerun
        st.session_state.clear()
        _clear_dev_session_cookie()
        return None


def _dev_auth() -> bool:
    """
    Development mode authentication (simple username/password).

    For local development only. Uses credentials from config.
    Uses cookie-based session persistence to survive page refreshes.

    Returns:
        bool: True if authenticated
    """
    if not _ensure_dev_auth_allowed():
        return False
    # Check if already logged in (dict-style access for test compatibility)
    if "authenticated" in st.session_state and st.session_state.get("authenticated", False):
        if _handle_session_timeout_check():
            return True
        else:
            st.rerun()

    # Try to restore session from cookie (survives page refresh)
    if _restore_dev_session_from_cookie():
        if _handle_session_timeout_check():
            return True
        else:
            st.rerun()

    # Initialize rate limiting state
    if "failed_login_attempts" not in st.session_state:
        st.session_state["failed_login_attempts"] = 0
        st.session_state["lockout_until"] = None

    # Check if locked out
    lockout_until = st.session_state.get("lockout_until")
    if lockout_until:
        if datetime.now(UTC) < lockout_until:
            remaining = (lockout_until - datetime.now(UTC)).seconds
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
                # Save session to cookie for persistence across page refresh
                _save_dev_session_to_cookie()
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
                    st.session_state["lockout_until"] = datetime.now(UTC) + timedelta(
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
    st.session_state["login_time"] = datetime.now(UTC)
    st.session_state["last_activity"] = datetime.now(UTC)
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
    OAuth2/OIDC authentication via FastAPI auth service with mTLS fallback.

    Production-ready authentication with Auth0 SSO integration.
    Validates HttpOnly session cookie set by FastAPI auth service.

    Component 6 (mTLS Fallback):
    - Checks IdP health monitor for fallback mode
    - If fallback active AND ENABLE_MTLS_FALLBACK=true → use mTLS auth
    - Otherwise → normal OAuth2/session validation

    Security Features:
    - HttpOnly cookies (XSS protection)
    - Secure + SameSite=Lax flags
    - Server-side session validation
    - AES-256-GCM encrypted session storage
    - Session binding (IP + User-Agent validation)
    - Absolute 4-hour timeout enforcement
    - mTLS fallback for IdP outages (admin-only)

    Flow:
    1. User accesses Streamlit page
    2. Check IdP health → if fallback mode + feature flag → mTLS auth
    3. Check for session_id cookie (HttpOnly, set by FastAPI /callback)
    4. If no cookie, redirect to /login (FastAPI initiates OAuth2 flow)
    5. If cookie exists, validate session with RedisSessionStore
    6. If valid, allow access; if invalid/expired, redirect to /login

    Returns:
        bool: True if authenticated
    """
    import asyncio

    # Component 6: Check mTLS fallback mode
    # Import fallback functions here to avoid circular imports
    from apps.web_console.auth.mtls_fallback import is_fallback_enabled

    # Component 6: Check if mTLS fallback should activate
    # This requires both: 1) IdP outage detected, 2) Feature flag enabled
    if is_fallback_enabled():
        try:
            # CRITICAL FIX: Use singleton to persist health check state across requests
            # Without this, consecutive failure counters reset to 0 on every request,
            # preventing fallback mode from ever activating.
            idp_checker = _get_idp_health_checker()

            # CRITICAL FIX: Trigger health check to update state
            # Without this call, the health checker never runs and fallback_mode
            # stays permanently False even during real Auth0 outages.
            # Performance optimization: Only check if interval has elapsed (throttling)
            # Use try-except with asyncio compatibility for Streamlit
            if idp_checker.should_check_now():
                try:
                    asyncio.run(idp_checker.check_health())
                except RuntimeError:
                    # If event loop already running (e.g., in Jupyter), use nest_asyncio
                    try:
                        import nest_asyncio

                        nest_asyncio.apply()
                        asyncio.run(idp_checker.check_health())
                    except ImportError:
                        logger.warning(
                            "nest_asyncio not installed, cannot run health check in existing event loop"
                        )

            # Check if fallback mode is active (requires 3 consecutive IdP failures)
            if idp_checker.is_fallback_mode():
                auth0_domain = os.getenv("AUTH0_DOMAIN", "")
                logger.warning(
                    "mTLS fallback mode active - using certificate authentication",
                    extra={"auth0_domain": auth0_domain},
                )

                # Display fallback mode banner to admins
                st.warning(
                    "⚠️ **Admin Fallback Mode Active**\n\n"
                    "Auth0 IdP is currently unavailable. Using mTLS certificate authentication.\n\n"
                    "Only administrators with pre-distributed client certificates can access the system."
                )

                # Attempt mTLS fallback authentication
                return _mtls_fallback_auth()

        except ValueError as e:
            # AUTH0_DOMAIN not configured - fallback disabled
            logger.debug(f"mTLS fallback check skipped: {e}")
        except Exception as e:
            # Health check failed - log but don't block OAuth2 login
            logger.error(f"IdP health check exception: {e}")

    # Check if already authenticated (cached in Streamlit session_state)
    if "authenticated" in st.session_state and st.session_state.get("authenticated", False):
        # Check session timeout
        if _check_session_timeout():
            return True
        else:
            # Session expired - clear and redirect to login
            st.session_state.clear()
            st.rerun()

    # Get session cookie
    session_id = get_session_cookie()

    if not session_id:
        # No session cookie - redirect to login page
        # Use st.switch_page for proper Streamlit navigation (CSP-friendly)
        st.switch_page("pages/login.py")
        st.stop()
        return False

    # Get client info for session binding
    client_ip = _get_client_ip()
    user_agent = _get_request_headers().get("User-Agent", "unknown")

    # Validate session (async operation)
    # HIGH FIX: Reuse cached session store to avoid creating new Redis connections per request
    async def _validate() -> dict[str, Any] | None:
        from apps.web_console.auth.session_manager import _get_session_store

        session_store = _get_session_store()
        # Validate session with IP/UA binding
        return await validate_session(session_id, session_store, client_ip, user_agent)

    try:
        # Run async validation
        user_info = asyncio.run(_validate())
    except RuntimeError:
        # If event loop already running (e.g., in Jupyter), use nest_asyncio
        try:
            import nest_asyncio

            nest_asyncio.apply()
            user_info = asyncio.run(_validate())
        except ImportError:
            logger.error("nest_asyncio not installed, cannot validate session")
            # Prometheus: Record authorization failure
            authorization_total.labels(result="failure").inc()
            authorization_failures_total.labels(reason="nest_asyncio_missing").inc()
            session_signature_failures_total.labels(reason="runtime_error").inc()
            st.error("Session validation failed. Please login again.")
            st.markdown(f"[Login]({os.getenv('OAUTH2_LOGIN_URL', '/login')})")
            st.stop()
            return False
        except Exception as nested_e:
            logger.error(f"Session validation exception (nested): {nested_e}")
            # Prometheus: Record authorization failure with specific exception type
            authorization_total.labels(result="failure").inc()
            reason = type(nested_e).__name__.lower()[:64]  # Bounded cardinality
            authorization_failures_total.labels(reason=reason).inc()
            session_signature_failures_total.labels(reason=reason).inc()
            st.error("Session validation failed. Please login again.")
            st.markdown(f"[Login]({os.getenv('OAUTH2_LOGIN_URL', '/login')})")
            st.stop()
            return False
    except ValueError as e:
        # Config errors (SESSION_ENCRYPTION_KEY missing or wrong size)
        logger.error(f"Session validation config error: {e}")
        # Prometheus: Record authorization failure
        authorization_total.labels(result="failure").inc()
        authorization_failures_total.labels(reason="config_error").inc()
        session_signature_failures_total.labels(reason="config_error").inc()
        st.error("Session validation failed. Please login again.")
        st.markdown(f"[Login]({os.getenv('OAUTH2_LOGIN_URL', '/login')})")
        st.stop()
        return False
    except Exception as e:
        logger.error(f"Session validation exception: {e}")
        # Prometheus: Record authorization failure with specific exception type
        authorization_total.labels(result="failure").inc()
        # Use exception class name as reason (bounded cardinality, truncated to 64 chars)
        reason = type(e).__name__.lower()[:64]
        authorization_failures_total.labels(reason=reason).inc()
        session_signature_failures_total.labels(reason=reason).inc()
        st.error("Session validation failed. Please login again.")
        st.markdown(f"[Login]({os.getenv('OAUTH2_LOGIN_URL', '/login')})")
        st.stop()
        return False

    if not user_info:
        # Invalid/expired session - redirect to login page
        # Prometheus: Record session signature failure (expired or invalid)
        session_signature_failures_total.labels(reason="invalid_or_expired").inc()
        # Use st.switch_page for proper Streamlit navigation (CSP-friendly)
        st.switch_page("pages/login.py")
        st.stop()
        return False

    # CRITICAL SECURITY (Component 3 - Codex High #1):
    # Store ONLY non-sensitive metadata in session_state.
    # Tokens (access_token, refresh_token, id_token) remain in encrypted Redis.
    # Use api_client.py helpers (get_access_token_from_redis) when tokens needed.
    # CRITICAL FIX (Codex High #3 - Iteration 2): Use UTC-aware datetime to prevent
    # TypeError when session_status.py mixes naive/aware datetimes.
    now = datetime.now(UTC)
    st.session_state["authenticated"] = True
    st.session_state["username"] = user_info["email"]  # Use email as display name
    st.session_state["auth_method"] = "oauth2"
    st.session_state["login_time"] = now  # Use now as login time for timeout tracking
    st.session_state["last_activity"] = now
    st.session_state["session_id"] = session_id
    st.session_state["user_id"] = user_info["user_id"]

    # Store non-sensitive user info for session status UI (Component 4)
    st.session_state["user_info"] = {
        "email": user_info["email"],
        "user_id": user_info["user_id"],
        "display_name": user_info.get("display_name", user_info["email"].split("@")[0]),
        "created_at": user_info.get("created_at"),
        "last_activity": user_info.get("last_activity"),
        "access_token_expires_at": user_info.get("access_token_expires_at"),
        # NEVER include: access_token, refresh_token, id_token
    }

    # Audit successful validation (not a new login, but session validation)
    # Only log on first validation to avoid spam
    if "oauth2_logged" not in st.session_state:
        _audit_successful_login(user_info["email"], "oauth2")
        st.session_state["oauth2_logged"] = True

        # Prometheus: Record successful session creation (only on first validation)
        session_created_total.inc()
        authorization_total.labels(result="success").inc()

    # Prometheus: Update active sessions count (throttled to 60s interval)
    # CRITICAL: Must be outside oauth2_logged guard to refresh on subsequent reruns
    _update_active_sessions_count()

    return True


def _init_session(username: str, auth_method: str) -> None:
    """
    Initialize authenticated session.

    Args:
        username: Authenticated user
        auth_method: Authentication method used (dev, basic, oauth2)
    """
    now = datetime.now(UTC)
    st.session_state["authenticated"] = True
    st.session_state["username"] = username
    st.session_state["auth_method"] = auth_method
    st.session_state["login_time"] = now
    st.session_state["last_activity"] = now
    st.session_state["session_id"] = _generate_session_id(username, now)
    # Dev/basic auth needs explicit RBAC context for API headers.
    from apps.web_console.config import DEV_ROLE, DEV_SESSION_VERSION, DEV_STRATEGIES, DEV_USER_ID

    st.session_state.setdefault("user_id", DEV_USER_ID or username)
    st.session_state.setdefault("role", DEV_ROLE)
    st.session_state.setdefault("strategies", DEV_STRATEGIES)
    st.session_state.setdefault("session_version", DEV_SESSION_VERSION)

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
    now = datetime.now(UTC)

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
    Terminate session when session times out (mTLS mode only).

    Uses SessionManager to properly revoke tokens and clean up session index.

    Args:
        timeout_type: Type of timeout ("idle_timeout" or "absolute_timeout")
    """
    auth_method = st.session_state.get("auth_method", "unknown")
    if auth_method != "mtls":
        return

    session_id = st.session_state.get("session_id")
    if not session_id:
        return

    try:
        session_manager = _get_session_manager()
        session_manager.terminate_session(session_id)
        logger.info(
            f"Terminated session on {timeout_type}: session_id={session_id}, "
            f"user={st.session_state.get('username', 'unknown')}"
        )
    except Exception as e:
        # Log error but don't block timeout handling
        logger.error(f"Failed to terminate session on {timeout_type}: {e}")


def _generate_session_id(username: str, login_time: datetime) -> str:
    """
    Generate unique session ID.

    Args:
        username: Username
        login_time: Login timestamp

    Returns:
        str: Session ID (random, URL-safe)
    """
    _ = username, login_time
    return secrets.token_urlsafe(32)


# ============================================================================
# Dev Mode Cookie-Based Session Persistence
# ============================================================================
# These functions enable session persistence across page refreshes in dev mode
# by storing session data in Redis and using a session cookie to identify sessions.

_DEV_SESSION_PREFIX = "web_console:dev_session:"
_DEV_SESSION_COOKIE_NAME = "dev_session_id"
_DEV_SESSION_TTL_SECONDS = 4 * 60 * 60  # 4 hours (matches SESSION_ABSOLUTE_TIMEOUT_HOURS)
# Session DB: Use REDIS_SESSION_DB env var for consistency with OAuth2 sessions
# Default to DB 1 (separate from job queue on DB 0)
_REDIS_SESSION_DB = int(os.environ.get("REDIS_SESSION_DB", "1"))
_DEV_SESSION_HMAC_SECRET = os.getenv("WEB_CONSOLE_DEV_SESSION_SECRET", "").strip()
if not _DEV_SESSION_HMAC_SECRET:
    _DEV_SESSION_HMAC_SECRET = secrets.token_urlsafe(32)
    logger.warning(
        "WEB_CONSOLE_DEV_SESSION_SECRET not set; using ephemeral secret for dev session cookies."
    )


def _ensure_dev_auth_allowed() -> bool:
    """Guard: dev auth is only allowed in local/dev environments."""
    env = os.getenv("ENVIRONMENT", "dev").lower()
    if env not in _ALLOWED_DEV_AUTH_ENVIRONMENTS:
        st.error(
            "🔒 Dev auth is disabled outside local/dev environments.\n\n"
            f"ENVIRONMENT must be one of {sorted(_ALLOWED_DEV_AUTH_ENVIRONMENTS)}. "
            f"Current ENVIRONMENT='{env or '(unset)'}'."
        )
        logger.error(
            "Dev auth blocked: ENVIRONMENT not allowed",
            extra={"environment": env or "(unset)"},
        )
        return False
    if not _is_localhost_request():
        st.error("🔒 Dev auth is restricted to localhost (127.0.0.1 or ::1).")
        logger.error(
            "Dev auth blocked: non-localhost request",
            extra={"client_ip": _get_client_ip()},
        )
        return False
    return True


def _is_localhost_request() -> bool:
    """Return True if request host is localhost/loopback or Docker bridge."""
    loopback_values = {"localhost", "127.0.0.1", "::1"}
    remote_addr = _get_remote_addr()

    # Allow localhost/loopback
    if remote_addr in loopback_values:
        return True

    # Allow Docker bridge networks (172.16.0.0/12 range covers 172.16-31.x.x)
    # This enables dev auth when running Streamlit in Docker with port mapping
    if remote_addr.startswith("172."):
        try:
            second_octet = int(remote_addr.split(".")[1])
            if 16 <= second_octet <= 31:
                return True
        except (ValueError, IndexError):
            pass

    return False


def _sign_dev_session_id(session_id: str) -> str:
    """Return hex HMAC-SHA256 signature for session_id."""
    secret = _DEV_SESSION_HMAC_SECRET.encode("utf-8")
    return hmac.new(secret, session_id.encode("utf-8"), hashlib.sha256).hexdigest()


def _format_dev_session_cookie_value(session_id: str) -> str:
    signature = _sign_dev_session_id(session_id)
    return f"{session_id}.{signature}"


def _parse_dev_session_cookie_value(value: str) -> str | None:
    """Return session_id if cookie signature is valid, else None."""
    if "." not in value:
        return None
    session_id, signature = value.rsplit(".", 1)
    expected = _sign_dev_session_id(session_id)
    if not hmac.compare_digest(expected, signature):
        return None
    return session_id


def _get_dev_session_redis() -> redis.Redis | None:
    """Get Redis client for dev session storage.

    Uses REDIS_SESSION_DB env var (default: 1) to match OAuth2 session storage.
    This keeps sessions separate from the job queue (DB 0).
    """
    global _dev_session_redis

    if _dev_session_redis is not None:
        try:
            _dev_session_redis.ping()
            return _dev_session_redis
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError, redis.exceptions.RedisError) as e:
            logger.warning(f"Cached dev session Redis client unhealthy: {e}")
            try:
                _dev_session_redis.close()
            except Exception:
                pass
            _dev_session_redis = None

    try:
        redis_host = os.environ.get("REDIS_HOST", "localhost")
        redis_port = int(os.environ.get("REDIS_PORT", "6379"))
        client = redis.Redis(
            host=redis_host,
            port=redis_port,
            db=_REDIS_SESSION_DB,  # Use env var for consistency with OAuth2 sessions
            decode_responses=True,
            socket_timeout=2,
        )
        client.ping()
        _dev_session_redis = client
        return _dev_session_redis
    except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError, redis.exceptions.RedisError) as e:
        logger.warning(f"Failed to connect to Redis for dev session: {e}")
        return None


def _save_dev_session_to_cookie() -> None:
    """Save current session to Redis and set a session cookie via JavaScript."""
    session_id = st.session_state.get("session_id")
    if not session_id:
        return

    # Store session data in Redis
    redis_client = _get_dev_session_redis()
    if redis_client:
        try:
            login_time = st.session_state.get("login_time")
            last_activity = st.session_state.get("last_activity")
            session_data = json.dumps(
                {
                    "username": st.session_state.get("username"),
                    "auth_method": st.session_state.get("auth_method"),
                    "login_time": login_time.isoformat()
                    if isinstance(login_time, datetime)
                    else None,
                    "last_activity": last_activity.isoformat()
                    if isinstance(last_activity, datetime)
                    else None,
                    "user_id": st.session_state.get("user_id"),
                    "role": st.session_state.get("role"),
                    "strategies": st.session_state.get("strategies", []),
                    "session_version": st.session_state.get("session_version"),
                }
            )
            redis_client.setex(
                f"{_DEV_SESSION_PREFIX}{session_id}",
                _DEV_SESSION_TTL_SECONDS,
                session_data,
            )
            logger.debug(f"Saved dev session to Redis: {session_id}")
        except Exception as e:
            logger.warning(f"Failed to save dev session to Redis: {e}")
            return

    # Set cookie via JavaScript (Streamlit doesn't have native cookie support)
    # Use a hidden component to inject JavaScript
    secure_flag = "" if _is_localhost_request() else "; Secure"
    cookie_value = _format_dev_session_cookie_value(session_id)
    cookie_js = f"""
    <script>
    document.cookie = "{_DEV_SESSION_COOKIE_NAME}={cookie_value}; path=/; max-age={_DEV_SESSION_TTL_SECONDS}; SameSite=Lax{secure_flag}";
    </script>
    """
    st.components.v1.html(cookie_js, height=0)


def _get_dev_session_cookie_raw() -> str | None:
    """Get raw dev session cookie value from request headers."""
    try:
        headers = _get_request_headers()
        cookie_header = headers.get("Cookie", "")
        if not cookie_header:
            return None

        # Parse cookies
        for cookie in cookie_header.split(";"):
            cookie = cookie.strip()
            if cookie.startswith(f"{_DEV_SESSION_COOKIE_NAME}="):
                return cookie[len(f"{_DEV_SESSION_COOKIE_NAME}=") :]
        return None
    except Exception as e:
        logger.debug(f"Failed to get dev session cookie: {e}")
        return None


def _get_dev_session_cookie() -> str | None:
    """Return validated dev session_id from cookie (HMAC-verified)."""
    raw_value = _get_dev_session_cookie_raw()
    if not raw_value:
        return None
    session_id = _parse_dev_session_cookie_value(raw_value)
    if not session_id:
        logger.warning("Invalid dev session cookie signature")
        return None
    return session_id


def _restore_dev_session_from_cookie() -> bool:
    """Restore session from Redis using cookie session ID."""
    raw_value = _get_dev_session_cookie_raw()
    if not raw_value:
        return False

    session_id = _parse_dev_session_cookie_value(raw_value)
    if not session_id:
        logger.warning("Rejected dev session cookie with invalid signature")
        _clear_dev_session_cookie()
        return False

    redis_client = _get_dev_session_redis()
    if not redis_client:
        return False

    try:
        session_data_raw = redis_client.get(f"{_DEV_SESSION_PREFIX}{session_id}")
        if not session_data_raw:
            logger.debug(f"Dev session not found in Redis: {session_id}")
            return False

        if isinstance(session_data_raw, bytes):
            session_data_raw = session_data_raw.decode("utf-8")
        session_data = json.loads(cast(str, session_data_raw))
        from apps.web_console.config import DEV_SESSION_VERSION

        stored_version = session_data.get("session_version")
        if stored_version != DEV_SESSION_VERSION:
            logger.warning(
                "Rejected dev session due to version mismatch",
                extra={
                    "session_id": session_id,
                    "stored_version": stored_version,
                    "expected_version": DEV_SESSION_VERSION,
                },
            )
            try:
                redis_client.delete(f"{_DEV_SESSION_PREFIX}{session_id}")
            except Exception as delete_error:
                logger.debug(f"Failed to delete version-mismatched dev session: {delete_error}")
            _clear_dev_session_cookie()
            return False

        # Restore session state
        st.session_state["authenticated"] = True
        st.session_state["username"] = session_data.get("username")
        st.session_state["auth_method"] = session_data.get("auth_method", "dev")
        st.session_state["session_id"] = session_id
        st.session_state["user_id"] = session_data.get("user_id")
        st.session_state["role"] = session_data.get("role")
        st.session_state["strategies"] = session_data.get("strategies", [])
        st.session_state["session_version"] = session_data.get("session_version")

        # Parse datetime fields
        if session_data.get("login_time"):
            st.session_state["login_time"] = datetime.fromisoformat(session_data["login_time"])
        if session_data.get("last_activity"):
            st.session_state["last_activity"] = datetime.fromisoformat(
                session_data["last_activity"]
            )

        # Update last activity and save back to Redis
        st.session_state["last_activity"] = datetime.now(UTC)
        _update_dev_session_in_redis(session_id)

        logger.info(f"Restored dev session from cookie: {session_id}")
        return True

    except Exception as e:
        logger.warning(f"Failed to restore dev session: {e}")
        return False


def _update_dev_session_in_redis(session_id: str) -> None:
    """Update last_activity in Redis session."""
    redis_client = _get_dev_session_redis()
    if not redis_client:
        return

    try:
        login_time = st.session_state.get("login_time")
        last_activity = st.session_state.get("last_activity")
        session_data = json.dumps(
            {
                "username": st.session_state.get("username"),
                "auth_method": st.session_state.get("auth_method"),
                "login_time": login_time.isoformat() if isinstance(login_time, datetime) else None,
                "last_activity": last_activity.isoformat()
                if isinstance(last_activity, datetime)
                else None,
                "user_id": st.session_state.get("user_id"),
                "role": st.session_state.get("role"),
                "strategies": st.session_state.get("strategies", []),
                "session_version": st.session_state.get("session_version"),
            }
        )
        # Refresh TTL on activity
        redis_client.setex(
            f"{_DEV_SESSION_PREFIX}{session_id}",
            _DEV_SESSION_TTL_SECONDS,
            session_data,
        )
    except Exception as e:
        logger.debug(f"Failed to update dev session in Redis: {e}")


def _clear_dev_session_cookie() -> None:
    """Clear dev session cookie and delete from Redis."""
    session_id = _get_dev_session_cookie()
    if session_id:
        # Delete from Redis
        redis_client = _get_dev_session_redis()
        if redis_client:
            try:
                redis_client.delete(f"{_DEV_SESSION_PREFIX}{session_id}")
            except Exception as e:
                logger.debug(f"Failed to delete dev session from Redis: {e}")

    # Clear cookie via JavaScript
    secure_flag = "" if _is_localhost_request() else "; Secure"
    cookie_js = f"""
    <script>
    document.cookie = "{_DEV_SESSION_COOKIE_NAME}=; path=/; max-age=0; SameSite=Lax{secure_flag}";
    </script>
    """
    st.components.v1.html(cookie_js, height=0)


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

        # CRITICAL: Do NOT fallback to X-Real-IP header here.
        # We use remote_addr to VALIDATE headers (trusted proxy check).
        # Reading headers to get remote_addr would create a circular trust vulnerability.

    except Exception as e:
        logger.debug(f"Failed to extract remote_addr: {e}")
        return ""


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
        - Get remote_addr (immediate upstream caller)
        - If TRUSTED_PROXY_IPS not set: Returns remote_addr (safe default for dev/docker)
        - If TRUSTED_PROXY_IPS set: Verifies remote_addr is trusted before using XFF
        - Falls back to remote_addr if verification fails or header extraction fails

    Returns:
        str: Client IP address from X-Forwarded-For (if from trusted proxy) or remote_addr
    """
    # Get remote_addr (immediate upstream caller)
    remote_addr = _get_remote_addr()

    # If no trusted proxies configured, return remote_addr (safe default for dev/docker)
    if not TRUSTED_PROXY_IPS:
        return remote_addr

    # Extract X-Forwarded-For header from nginx
    try:
        # Defense-in-depth: Only trust X-Forwarded-For if request came from trusted proxy
        # This prevents spoofing if Streamlit container is directly reachable
        if remote_addr not in TRUSTED_PROXY_IPS:
            logger.warning(
                f"Request from untrusted proxy {remote_addr} (not in TRUSTED_PROXY_IPS). "
                "Ignoring X-Forwarded-For to prevent IP spoofing. Using remote_addr."
            )
            return remote_addr

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

        # No X-Forwarded-For header - fall back to remote_addr
        logger.debug(f"No X-Forwarded-For header found, using remote_addr: {remote_addr}")
        return remote_addr
    except Exception as e:
        logger.warning(f"Failed to extract client IP: {e}. Using remote_addr.")
        return remote_addr


def audit_to_database(
    user_id: str,
    action: str,
    details: dict[str, Any],
    reason: str | None = None,
    session_id: str | None = None,
    *,
    event_type: str = "auth",
    resource_type: str = "system",
    resource_id: str | None = None,
    outcome: str = "success",
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
        "event_type": event_type,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "outcome": outcome,
    }

    try:
        import psycopg
        from psycopg.types.json import Jsonb

        # Use shared connection pool to avoid per-entry connection overhead.
        with _get_audit_db_pool().connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO audit_log (
                        user_id,
                        action,
                        details,
                        reason,
                        ip_address,
                        session_id,
                        event_type,
                        resource_type,
                        resource_id,
                        outcome
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        user_id,
                        action,
                        Jsonb(details),
                        reason,
                        ip_address,
                        session_id or "N/A",
                        event_type,
                        resource_type,
                        resource_id,
                        outcome,
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
        "timestamp": datetime.now(UTC).isoformat(),
    }
    session_id = st.session_state.get("session_id", "unknown")
    audit_to_database(
        user_id=username,
        action="login_success",
        details=details,
        session_id=session_id,
        event_type="auth",
        resource_type="session",
        resource_id="login",
        outcome="success",
    )


def _audit_failed_login(auth_method: str) -> None:
    """
    Audit failed login attempt.

    Args:
        auth_method: Authentication method attempted
    """
    details = {
        "auth_method": auth_method,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    audit_to_database(
        user_id="<failed_login_attempt>",
        action="login_failed",
        details=details,
        session_id="N/A",  # No session for failed login
        event_type="auth",
        resource_type="session",
        resource_id="login",
        outcome="denied",
    )


def _get_request_headers() -> dict[str, str]:
    """
    Get request headers from Streamlit context.

    Attempts to access nginx-forwarded headers using Streamlit's session_info API.

    Implementation Notes:
    - Streamlit exposes headers via session_info.ws.request (WebSocket request object)
    - This is the recommended approach for production deployment behind nginx

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
        logger.debug(f"Could not access session_info headers: {e}.")

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
    # RFC 4514: commas can be escaped (\,), so split on commas not preceded by backslash
    # DN format: "CN=value,OU=value,O=value,C=value"
    def _unescape_dn_value(value: str) -> str:
        return re.sub(r"\\(.)", r"\1", value)

    components = re.split(r"(?<!\\),", dn)
    for component in components:
        component = component.strip()
        if component.upper().startswith("CN="):
            return _unescape_dn_value(component[3:])  # Remove "CN=" prefix

    # Fallback: Return full DN if CN not found
    logger.warning(f"CN not found in DN: {dn}")
    return dn


def _issue_jwt_for_client_dn(
    client_dn: str, client_cn: str, client_verify: str
) -> tuple[str, dict[str, Any]] | tuple[None, None]:
    """
    Create session and issue JWT token pair for authenticated client.

    Uses SessionManager for comprehensive session management including:
    - Session limits enforcement (max_sessions_per_user)
    - Automatic eviction of oldest sessions when limit exceeded
    - Session index tracking for user_id
    - Token pair generation (access + refresh)
    - Session metadata storage in Redis

    JWT Claims (via SessionManager):
    - sub: Client DN (CRITICAL: Used for JWT-DN binding contract)
    - typ: "access" or "refresh" (token type)
    - iat: Issued at timestamp
    - exp: Expiration timestamp (from AuthConfig)
    - jti: Unique JWT ID (Redis-backed revocation)
    - aud: Audience claim (from AuthConfig)
    - iss: Issuer claim (from AuthConfig)
    - session_binding: Hash of IP + User Agent (session hijacking prevention)

    Args:
        client_dn: Full Distinguished Name from client certificate
        client_cn: Common Name from client certificate
        client_verify: Verification status from nginx (should be "SUCCESS")

    Returns:
        tuple: (access_token string, claims dict) on success, (None, None) on failure
    """
    try:
        # Get SessionManager instance
        session_manager = _get_session_manager()

        # Get client info for session binding
        headers = _get_request_headers()
        client_ip = _get_client_ip()
        user_agent = headers.get("User-Agent", "unknown")

        # Fail-closed: If TRUSTED_PROXY_IPS configured but IP extraction failed (localhost),
        # reject token issuance to prevent silently downgrading session binding strength
        if TRUSTED_PROXY_IPS and client_ip == "localhost":
            logger.error(
                f"Session creation failed for {client_dn}: Cannot determine real client IP "
                "(got localhost). TRUSTED_PROXY_IPS is configured but X-Forwarded-For extraction failed. "
                "This prevents silently downgrading session binding security."
            )
            return None, None

        # Create session with SessionManager (enforces session limits and rate limiting)
        # Returns (access_token, refresh_token) tuple
        try:
            access_token, refresh_token = session_manager.create_session(
                user_id=client_dn,  # DN as user_id (JWT.sub == client DN for binding)
                client_ip=client_ip,
                user_agent=user_agent,
            )
        except SessionLimitExceededError as e:
            logger.error(f"Session creation failed for {client_dn}: Session limit exceeded. {e}")
            return None, None
        except RateLimitExceededError as e:
            logger.error(f"Session creation failed for {client_dn}: Rate limit exceeded. {e}")
            return None, None

        # Decode access token (without verification) to get claims for display
        # This is safe because we just generated it and need the claims for session_state
        claims = jwt.decode(access_token, options={"verify_signature": False})

        # Add custom fields for backward compatibility with existing code
        claims["cn"] = client_cn
        claims["cert_verify"] = client_verify

        logger.info(
            f"Created session for {client_cn} (DN: {client_dn}, "
            f"session_id: {claims.get('session_id', 'unknown')}, jti: {claims.get('jti', 'unknown')})"
        )

        return access_token, claims

    except Exception as e:
        logger.error(f"Failed to create session for {client_dn}: {e}")
        return None, None


def _validate_jwt_dn_binding(headers: dict[str, str], jwt_token: str) -> bool:
    """
    Validate JWT-DN binding contract on EVERY request.

    Uses SessionManager for comprehensive validation including:
    - RS256 signature validation
    - Token expiration checks
    - Redis JTI revocation checks
    - Session binding validation (IP + User Agent)
    - Session metadata validation

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
        jwt_token: JWT access token from session state

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

        # Get SessionManager instance
        session_manager = _get_session_manager()

        # Get current client info for session binding validation
        client_ip = _get_client_ip()
        current_ua = headers.get("User-Agent", "unknown")

        # Fail-closed: If TRUSTED_PROXY_IPS configured but IP extraction failed (localhost),
        # reject validation even if SessionManager validation passed
        # This prevents token replay when header extraction degrades
        if TRUSTED_PROXY_IPS and client_ip == "localhost":
            logger.error(
                "JWT validation failed: Cannot determine real client IP (got localhost). "
                "TRUSTED_PROXY_IPS is configured but X-Forwarded-For extraction failed. "
                "Rejecting to prevent token replay with degraded IP detection."
            )
            return False

        # Validate session using SessionManager (validates token + session binding)
        # SessionManager.validate_session signature: (access_token, client_ip, user_agent)
        try:
            claims = session_manager.validate_session(jwt_token, client_ip, current_ua)
        except TokenExpiredError:
            logger.warning("Session validation failed: Token expired")
            return False
        except TokenRevokedError:
            logger.error("Session validation failed: Token revoked (JTI blacklisted)")
            return False
        except InvalidTokenError as e:
            logger.error(
                f"Session validation failed: Invalid token or session binding mismatch: {e}"
            )
            return False

        # Validate JWT-DN binding contract: JWT.sub MUST equal current client DN
        jwt_dn = claims.get("sub", "")
        if jwt_dn != current_dn:
            logger.error(
                f"JWT-DN binding validation failed: JWT.sub={jwt_dn}, current DN={current_dn}"
            )
            return False

        # All checks passed (signature, expiration, revocation, DN binding, session binding)
        # Note: SessionManager already validated session binding (IP + User Agent)
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
        "user_id": st.session_state.get("user_id", st.session_state.get("username", "unknown")),
        "role": st.session_state.get("role"),
        "strategies": st.session_state.get("strategies", []),
        "session_version": st.session_state.get("session_version"),
    }


def _mtls_fallback_auth() -> bool:
    """
    mTLS fallback authentication for Auth0 IdP outages (Component 6).

    Admin-only authentication using pre-distributed client certificates.
    Only activates when:
    1. ENABLE_MTLS_FALLBACK=true (feature flag)
    2. IdP health monitor detects fallback mode (3+ consecutive failures)

    Security:
    - Certificate lifetime enforcement (7-day max)
    - CRL validation (fail-secure if CRL unavailable)
    - Admin CN allowlist validation
    - Comprehensive audit logging

    Returns:
        bool: True if authenticated via mTLS fallback
    """
    import asyncio
    import urllib.parse

    # Check if already authenticated via fallback
    if "authenticated" in st.session_state and st.session_state.get("authenticated", False):
        if st.session_state.get("auth_method") == "mtls_fallback":
            # Check session timeout
            if _check_session_timeout():
                return True
            else:
                # Session expired
                st.session_state.clear()
                st.rerun()

    # Get request headers
    headers = _get_request_headers()

    # Step 0: Verify request comes from trusted proxy (defense-in-depth)
    # Match _mtls_auth behavior to prevent header spoofing in fallback mode
    if not TRUSTED_PROXY_IPS:
        if not os.environ.get("ALLOW_INSECURE_MTLS_DEV", "").lower() == "true":
            st.error(
                "🔒 Configuration Error: mTLS fallback requires TRUSTED_PROXY_IPS.\n\n"
                "TRUSTED_PROXY_IPS environment variable is not configured.\n\n"
                "For production: Set TRUSTED_PROXY_IPS to your nginx proxy IP.\n"
                "For development: Set ALLOW_INSECURE_MTLS_DEV=true (insecure!)."
            )
            logger.error(
                "mTLS fallback rejected: TRUSTED_PROXY_IPS not configured. "
                "Set ALLOW_INSECURE_MTLS_DEV=true to allow dev mode (insecure)."
            )
            _audit_failed_login("mtls_fallback")
            return False
        logger.warning(
            "mTLS fallback running in INSECURE dev mode (ALLOW_INSECURE_MTLS_DEV=true). "
            "Headers are NOT verified. DO NOT use in production!"
        )
    else:
        remote_addr = _get_remote_addr()
        if remote_addr not in TRUSTED_PROXY_IPS:
            st.error(
                "🔒 Authentication failed: Request not from trusted proxy.\n\n"
                f"Source: {remote_addr}\n\n"
                "This may indicate a security issue. Please contact your administrator."
            )
            logger.error(
                f"mTLS fallback rejected: Request from untrusted source {remote_addr} "
                f"(not in TRUSTED_PROXY_IPS). Possible header spoofing attempt."
            )
            _audit_failed_login("mtls_fallback")
            return False

    # Get client certificate from nginx header (URL-encoded PEM)
    cert_pem_encoded = headers.get("X-SSL-Client-Cert", "")
    if not cert_pem_encoded:
        st.error(
            "🔒 mTLS Fallback Authentication Failed\n\n"
            "No client certificate provided. Only administrators with pre-distributed "
            "client certificates can access the system during IdP outages.\n\n"
            "Contact your administrator for emergency access credentials."
        )
        _audit_failed_login("mtls_fallback")
        return False

    # URL-decode certificate (nginx uses URL encoding for special characters)
    cert_pem = urllib.parse.unquote(cert_pem_encoded)

    # MEDIUM FIX: Use singleton validator to persist CRL cache across requests
    # Without this, the 1-hour CRL cache would be useless as validator is recreated
    # on every authentication attempt.
    try:
        validator = _get_mtls_fallback_validator()
    except ValueError:
        st.error(
            "🔒 mTLS Fallback Configuration Error\n\n"
            "Admin CN allowlist not configured (MTLS_ADMIN_CN_ALLOWLIST).\n\n"
            "Contact your administrator to configure fallback authentication."
        )
        logger.error("mTLS fallback rejected: MTLS_ADMIN_CN_ALLOWLIST not configured")
        _audit_failed_login("mtls_fallback")
        return False

    # Validate certificate (async operation for CRL check)
    # MEDIUM FIX: Use nested function + try-except for asyncio compatibility with Streamlit
    async def _validate_cert() -> "CertificateInfo":
        return await validator.validate_certificate(cert_pem, headers)

    try:
        cert_info = asyncio.run(_validate_cert())
    except RuntimeError:
        # If event loop already running (e.g., in Jupyter), use nest_asyncio
        try:
            import nest_asyncio

            nest_asyncio.apply()
            cert_info = asyncio.run(_validate_cert())
        except ImportError:
            logger.error("nest_asyncio not installed, cannot validate certificate")
            st.error("Certificate validation failed. Please contact your administrator.")
            _audit_failed_login("mtls_fallback")
            return False
    except Exception as e:
        logger.error(f"Certificate validation exception: {e}")
        st.error("Certificate validation failed. Please contact your administrator.")
        _audit_failed_login("mtls_fallback")
        return False

    # Check validation result
    if not cert_info.valid:
        st.error(
            f"🔒 mTLS Fallback Authentication Failed\n\n"
            f"Certificate validation error: {cert_info.error}\n\n"
            f"Contact your administrator for assistance."
        )
        logger.warning(
            f"mTLS fallback rejected: {cert_info.error}",
            extra={"cn": cert_info.cn, "fingerprint": cert_info.fingerprint},
        )
        _audit_failed_login("mtls_fallback")
        return False

    # Initialize session for authenticated admin
    st.session_state["authenticated"] = True
    st.session_state["username"] = cert_info.cn
    st.session_state["auth_method"] = "mtls_fallback"
    st.session_state["login_time"] = datetime.now(UTC)
    st.session_state["last_activity"] = datetime.now(UTC)
    st.session_state["session_id"] = _generate_session_id(cert_info.cn, datetime.now(UTC))

    # Store certificate info for audit
    st.session_state["cert_info"] = {
        "cn": cert_info.cn,
        "dn": cert_info.dn,
        "fingerprint": cert_info.fingerprint,
        "not_before": cert_info.not_before.isoformat(),
        "not_after": cert_info.not_after.isoformat(),
        "crl_status": cert_info.crl_status,
    }

    # Audit successful fallback authentication
    client_ip = _get_client_ip()
    audit_to_database(
        user_id=cert_info.cn,
        action="mtls_fallback_login_success",
        details={
            "auth_method": "mtls_fallback",
            "timestamp": datetime.now(UTC).isoformat(),
            "fingerprint": cert_info.fingerprint,
            "dn": cert_info.dn,
            "expires_at": cert_info.not_after.isoformat(),
            "crl_status": cert_info.crl_status,
        },
        session_id=st.session_state["session_id"],
        event_type="auth",
        resource_type="session",
        resource_id="mtls_fallback_login",
        outcome="success",
    )

    logger.info(
        "mTLS fallback authentication successful",
        extra={
            "cn": cert_info.cn,
            "fingerprint": cert_info.fingerprint,
            "client_ip": client_ip,
            "crl_status": cert_info.crl_status,
        },
    )

    return True


def logout() -> None:
    """Logout current user and clear session.

    For mTLS mode: Terminates session (revokes tokens, cleans up session index).
    For OAuth2 mode: Redirects to FastAPI /logout (clears cookie, redirects to Auth0 logout).
    For mTLS fallback mode: Clears session state and audit logs.
    For dev mode: Clears session cookie and Redis session.
    For other modes: Simply clears session state.
    """
    username = st.session_state.get("username", "unknown")
    auth_method = st.session_state.get("auth_method", "unknown")
    # Get session_id from session_state first (available in all modes)
    session_id = st.session_state.get("session_id")

    # Clear dev session cookie and Redis
    if auth_method == "dev":
        _clear_dev_session_cookie()

    # Terminate session for mTLS mode (revokes tokens and cleans up session index)
    if auth_method == "mtls":
        try:
            # Get session_id from JWT claims (more reliable than session_state)
            # Fallback to session_state if claims unavailable
            jwt_claims = st.session_state.get("jwt_claims", {})
            session_id = jwt_claims.get("session_id") or session_id

            if session_id:
                session_manager = _get_session_manager()
                session_manager.terminate_session(session_id)
                logger.info(
                    f"Terminated session on logout: session_id={session_id}, user={username}"
                )
            else:
                logger.warning(
                    f"Logout skipped session termination: No session_id found for user={username}"
                )
        except Exception as e:
            # Log error but don't block logout
            logger.error(f"Failed to terminate session on logout: {e}")

    details = {
        "timestamp": datetime.now(UTC).isoformat(),
        "auth_method": auth_method,
    }
    audit_to_database(
        user_id=username,
        action="logout",
        details=details,
        session_id=session_id,
        event_type="auth",
        resource_type="session",
        resource_id="logout",
        outcome="success",
    )

    st.session_state.clear()

    # For OAuth2, redirect to FastAPI /logout endpoint
    # FastAPI will:
    # 1. Get session_id from HttpOnly cookie
    # 2. Revoke refresh token at Auth0
    # 3. Delete session from Redis
    # 4. Redirect to Auth0 logout URL
    if auth_method == "oauth2":
        logout_url = os.getenv("OAUTH2_LOGOUT_URL", "/logout")

        # Show logout message and redirect link (CSP-friendly)
        st.title("Logging out...")
        st.info("You are being logged out. Please click the link below to complete logout.")
        st.markdown(f"**[Complete Logout]({logout_url})**")

        # Note: We can't use st.switch_page() here because logout is a FastAPI endpoint,
        # not a Streamlit page. User must click the link to trigger logout endpoint
        # which will handle token revocation and Auth0 logout redirect.
        st.stop()
    else:
        st.rerun()
