"""Streamlit session management via HttpOnly cookies.

This module provides utilities for Streamlit pages to validate session cookies
set by the FastAPI auth service. The session cookie is HttpOnly (not accessible
via JavaScript), so we read it from the request headers in Streamlit.

Usage in Streamlit pages:
    from apps.web_console.auth.session_manager import require_auth, get_current_user

    @require_auth
    def main():
        user_info = get_current_user()
        st.write(f"Welcome, {user_info['email']}")
"""

import asyncio
import logging
import os
from functools import lru_cache, wraps
from typing import Any

import redis.asyncio
import streamlit as st

from apps.web_console.auth.session_store import RedisSessionStore

logger = logging.getLogger(__name__)


def get_session_cookie() -> str | None:
    """Extract session_id from HttpOnly cookie.

    Streamlit exposes cookies via streamlit.web.server.websocket_headers
    or via browser's request headers. We use the standard approach of
    reading from the request context.

    Returns:
        Session ID from cookie, or None if not found
    """
    try:
        # Access cookies from Streamlit's request context
        # This requires Streamlit >=1.28.0 with cookie support
        from streamlit.web.server.websocket_headers import _get_websocket_headers

        headers = _get_websocket_headers()
        cookie_header = headers.get("Cookie", "") if headers else ""

        # Parse cookie header (format: "key1=value1; key2=value2")
        cookies = {}
        for cookie in cookie_header.split(";"):
            cookie = cookie.strip()
            if "=" in cookie:
                key, value = cookie.split("=", 1)
                cookies[key] = value

        return cookies.get("session_id")
    except Exception as e:
        logger.warning(f"Failed to extract session cookie: {e}")
        return None


async def validate_session(
    session_id: str,
    session_store: RedisSessionStore,
    client_ip: str,
    user_agent: str,
    db_pool: Any | None = None,
) -> dict[str, Any] | None:
    """Validate session ID and return ONLY non-sensitive metadata.

    CRITICAL SECURITY (Component 3 - Codex Critical #1):
    Returns ONLY non-sensitive user metadata. Tokens remain in Redis
    and are fetched by backend helpers when needed for API calls.

    Args:
        session_id: Session ID from cookie
        session_store: Redis session store instance
        client_ip: Client IP address for session binding validation
        user_agent: Client User-Agent for session binding validation

    Returns:
        Non-sensitive metadata dict (user_id, email, display_name, timestamps)
        or None if invalid. NEVER includes access_token, refresh_token, or id_token.
    """
    if not session_id:
        return None

    try:
        db_pool = db_pool or _maybe_get_db_pool()
        # Validate session with IP/UA binding enforcement
        session_data = await session_store.get_session(
            session_id,
            current_ip=client_ip,
            current_user_agent=user_agent,
            update_activity=True,  # Update last_activity timestamp
        )

        if not session_data:
            logger.info("Invalid or expired session", extra={"session_id": session_id[:8] + "..."})
            return None

        # Optional session_version validation (RBAC invalidation)
        if hasattr(session_data, "session_version"):
            if db_pool is None:
                # Fail closed when RBAC validation cannot be performed
                logger.error(
                    "session_version_validation_skipped_db_unavailable",
                    extra={"user_id": session_data.user_id},
                )
                await session_store.delete_session(session_id)
                return None

            from apps.web_console.auth.session_invalidation import validate_session_version

            is_valid = await validate_session_version(
                session_data.user_id,
                session_data.session_version,
                db_pool,
            )
            if not is_valid:
                logger.warning(
                    "session_version_mismatch",
                    extra={"user_id": session_data.user_id, "session_version": session_data.session_version},
                )
                await session_store.delete_session(session_id)
                return None

        # CRITICAL: Return ONLY non-sensitive metadata (NO TOKENS!)
        # Component 3 - Codex Critical #1 Fix

        # Backward compatibility: Default to created_at + 1h if field missing (old sessions)
        expires_at = session_data.access_token_expires_at
        if expires_at is None:
            from datetime import timedelta
            expires_at = session_data.created_at + timedelta(hours=1)

        return {
            "user_id": session_data.user_id,
            "email": session_data.email,
            "display_name": session_data.email.split("@")[0],  # Derive display name from email
            "created_at": session_data.created_at.isoformat(),
            "last_activity": session_data.last_activity.isoformat(),
            "access_token_expires_at": expires_at.isoformat(),
            "role": getattr(session_data, "role", None),
            "strategies": getattr(session_data, "strategies", []),
            "session_version": getattr(session_data, "session_version", 1),
            # NEVER include: access_token, refresh_token, id_token
        }
    except Exception as e:
        logger.error(f"Session validation error: {e}")
        return None


def require_auth(func: Any) -> Any:
    """Decorator to require authentication for Streamlit pages.

    Usage:
        @require_auth
        def main():
            st.write("Protected content")

    If user is not authenticated, redirects to /login.
    """

    @wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        # Get session cookie
        session_id = get_session_cookie()
        if not session_id:
            st.warning("You must be logged in to access this page.")
            st.markdown("[Login](/login)")
            st.stop()

        # Get client info for session binding
        # Import _get_client_ip and _get_request_headers from auth module
        # These functions properly validate trusted proxies and extract headers
        # CRITICAL: Import from parent module to access trusted proxy validation
        # This prevents hardcoded fallbacks that would bypass session binding
        from apps.web_console.auth import _get_client_ip, _get_request_headers

        # Get client IP and User-Agent with trusted proxy validation
        client_ip = _get_client_ip()
        user_agent = _get_request_headers().get("User-Agent", "unknown")

        async def _validate() -> dict[str, Any] | None:
            session_store = _get_session_store()
            # Pass IP/UA for session binding validation
            db_pool = _maybe_get_db_pool()
            return await validate_session(session_id, session_store, client_ip, user_agent, db_pool)

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
                st.error("Session validation failed. Please login again.")
                st.markdown("[Login](/login)")
                st.stop()

        if not user_info:
            st.warning("Your session has expired. Please log in again.")
            st.markdown("[Login](/login)")
            st.stop()

        # Cache user info in Streamlit session_state
        st.session_state["user_info"] = user_info

        return func(*args, **kwargs)

    return wrapper


def get_current_user() -> dict[str, Any]:
    """Get current authenticated user info.

    Must be called within a function decorated with @require_auth.

    Returns:
        Non-sensitive user metadata dict containing:
        - user_id: Auth0 user ID
        - email: User email address
        - display_name: Display name derived from email
        - created_at: Session creation timestamp (ISO 8601)
        - last_activity: Last activity timestamp (ISO 8601)
        - access_token_expires_at: Token expiry timestamp (ISO 8601)

        SECURITY (Component 3 - Codex Critical #1): Tokens (access_token,
        refresh_token, id_token) are NEVER included. Use api_client.py helpers
        to fetch tokens from Redis when needed for API calls.

    Raises:
        RuntimeError: If called without @require_auth
    """
    if "user_info" not in st.session_state:
        raise RuntimeError("get_current_user() must be called within @require_auth")

    return st.session_state["user_info"]  # type: ignore[no-any-return]


def _get_encryption_key() -> bytes:
    """Get session encryption key from environment (internal helper)."""
    import base64
    import os

    key_b64 = os.getenv("SESSION_ENCRYPTION_KEY")
    if not key_b64:
        raise ValueError("SESSION_ENCRYPTION_KEY environment variable not set")

    try:
        key_bytes = base64.b64decode(key_b64)
    except Exception as e:
        raise ValueError(f"SESSION_ENCRYPTION_KEY must be base64-encoded: {e}") from e

    if len(key_bytes) != 32:
        raise ValueError(f"SESSION_ENCRYPTION_KEY must decode to 32 bytes (got {len(key_bytes)})")

    return key_bytes


def _maybe_get_db_pool() -> Any | None:
    """Best-effort fetch of the Streamlit DB pool without hard dependency.

    Uses the cached `_get_db_pool` from `apps.web_console.app` when available.
    Returns None if the pool cannot be initialized (e.g., missing dependency).
    """

    try:
        from apps.web_console.app import _get_db_pool as app_get_db_pool

        return app_get_db_pool()
    except Exception:
        # Avoid blocking auth flows if DB is unavailable; caller handles None.
        logger.debug("db_pool_unavailable", exc_info=True)
        return None


@lru_cache
def _get_redis_client() -> redis.asyncio.Redis:
    """Module-level Redis client to avoid per-request connections."""

    return redis.asyncio.Redis(
        host=os.getenv("REDIS_HOST", "redis"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        db=1,
        decode_responses=False,
    )


@lru_cache
def _get_session_store() -> RedisSessionStore:
    """Shared session store backed by the cached Redis client."""

    return RedisSessionStore(
        redis_client=_get_redis_client(),
        encryption_key=_get_encryption_key(),
    )
