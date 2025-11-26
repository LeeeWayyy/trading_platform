"""Secure API client that fetches access tokens from Redis (Component 3).

CRITICAL SECURITY (Codex Critical #1 Fix):
This module provides the ONLY secure way to fetch access tokens in Streamlit.
Tokens are NEVER stored in st.session_state - they are always fetched from
encrypted Redis storage when needed for API calls.

Usage in Streamlit pages:
    from apps.web_console.auth.api_client import call_api_with_auth

    # Make authenticated API call
    response = await call_api_with_auth(
        url="https://api.trading-platform.local/positions",
        method="GET",
        session_id=st.context.cookies.get("session_id"),
        session_store=get_session_store(),
        client_ip=get_client_ip(),
        user_agent=get_user_agent(),
    )
"""

import logging
from typing import Any

import httpx

from apps.web_console.auth.session_store import RedisSessionStore

logger = logging.getLogger(__name__)


async def get_access_token_from_redis(
    session_id: str,
    session_store: RedisSessionStore,
    client_ip: str,
    user_agent: str,
) -> str | None:
    """Fetch access token from Redis session store.

    CRITICAL: This is the ONLY way to get access tokens in Streamlit.
    Tokens are NEVER stored in st.session_state.

    Args:
        session_id: Session ID from HttpOnly cookie
        session_store: Redis session store instance
        client_ip: Client IP address (for binding validation)
        user_agent: Client User-Agent (for binding validation)

    Returns:
        Access token if session valid, None if expired/invalid
    """
    session_data = await session_store.get_session(
        session_id,
        current_ip=client_ip,
        current_user_agent=user_agent,
        update_activity=False,  # Don't update activity for token fetch
    )

    if not session_data:
        logger.warning("Failed to fetch access token: session invalid")
        return None

    return session_data.access_token


async def call_api_with_auth(
    url: str,
    method: str = "GET",
    session_id: str | None = None,
    session_store: RedisSessionStore | None = None,
    client_ip: str | None = None,
    user_agent: str | None = None,
    **kwargs: Any,
) -> httpx.Response:
    """Call API with OAuth2 bearer token from Redis.

    Fetches access token from encrypted Redis storage and adds it to the
    Authorization header. Tokens are NEVER exposed to Streamlit session_state.

    Args:
        url: API endpoint URL
        method: HTTP method (GET, POST, PUT, DELETE, etc.)
        session_id: Session ID from HttpOnly cookie
        session_store: Redis session store instance
        client_ip: Client IP address (for binding validation)
        user_agent: Client User-Agent (for binding validation)
        **kwargs: Additional arguments for httpx.request (headers, data, json, etc.)

    Returns:
        HTTP response from API

    Raises:
        ValueError: If required parameters missing or session invalid

    Example:
        >>> response = await call_api_with_auth(
        ...     url="https://api.trading-platform.local/positions",
        ...     method="GET",
        ...     session_id=session_id,
        ...     session_store=session_store,
        ...     client_ip=client_ip,
        ...     user_agent=user_agent,
        ... )
        >>> positions = response.json()
    """
    if not all([session_id, session_store, client_ip, user_agent]):
        raise ValueError(
            "Missing required parameters: session_id, session_store, client_ip, user_agent"
        )

    # Type assertions after validation
    assert session_id is not None
    assert session_store is not None
    assert client_ip is not None
    assert user_agent is not None

    # Fetch access token from Redis (NEVER from session_state!)
    access_token = await get_access_token_from_redis(
        session_id, session_store, client_ip, user_agent
    )

    if not access_token:
        raise ValueError("Session invalid or expired")

    # Add Authorization header
    headers = kwargs.get("headers", {})
    headers["Authorization"] = f"Bearer {access_token}"
    kwargs["headers"] = headers

    # Make API call
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.request(method, url, **kwargs)
        return response
