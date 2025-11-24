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

import logging
from functools import wraps
from typing import Any

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
) -> dict[str, Any] | None:
    """Validate session ID and return session data.

    Args:
        session_id: Session ID from cookie
        session_store: Redis session store instance
        client_ip: Client IP address for session binding validation
        user_agent: Client User-Agent for session binding validation

    Returns:
        Session data dict (user_id, email, access_token, etc.) or None if invalid
    """
    if not session_id:
        return None

    try:
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

        # Convert SessionData to dict for Streamlit state
        return {
            "user_id": session_data.user_id,
            "email": session_data.email,
            "access_token": session_data.access_token,
            "created_at": session_data.created_at.isoformat(),
            "last_activity": session_data.last_activity.isoformat(),
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
        # Check if already authenticated (cached in Streamlit session_state)
        if "user_info" in st.session_state:
            return func(*args, **kwargs)

        # Get session cookie
        session_id = get_session_cookie()
        if not session_id:
            st.warning("You must be logged in to access this page.")
            st.markdown("[Login](/login)")
            st.stop()

        # Get client info for session binding
        # Import _get_client_ip and _get_request_headers from auth module
        # These functions properly validate trusted proxies and extract headers
        import asyncio
        import os

        import redis.asyncio

        # CRITICAL: Import from parent module to access trusted proxy validation
        # This prevents hardcoded fallbacks that would bypass session binding
        from apps.web_console.auth import _get_client_ip, _get_request_headers
        from apps.web_console.auth.session_store import RedisSessionStore

        # Get client IP and User-Agent with trusted proxy validation
        client_ip = _get_client_ip()
        user_agent = _get_request_headers().get("User-Agent", "unknown")

        async def _validate() -> dict[str, Any] | None:
            redis_client = redis.asyncio.Redis(
                host=os.getenv("REDIS_HOST", "redis"),
                port=int(os.getenv("REDIS_PORT", "6379")),
                db=1,  # Sessions DB
                decode_responses=False,
            )
            session_store = RedisSessionStore(
                redis_client=redis_client,
                encryption_key=_get_encryption_key(),
            )
            # Pass IP/UA for session binding validation
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
        User info dict with user_id, email, access_token, etc.

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
