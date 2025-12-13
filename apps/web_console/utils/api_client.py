"""Shared API client and helpers for web console pages.

This module centralizes common API-related utilities used across pages:
- safe_current_user(): Safely get current user from session
- get_auth_headers(): Build X-User-* headers for API requests
- fetch_api(): Fetch from API endpoint with auth headers
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

import requests

from apps.web_console.auth.permissions import get_authorized_strategies
from apps.web_console.auth.session_manager import get_current_user
from apps.web_console.config import API_REQUEST_TIMEOUT, ENDPOINTS


def safe_current_user() -> Mapping[str, Any]:
    """Return current user when session context exists.

    Streamlit tests render components without an authenticated session; in those
    cases fall back to an empty mapping so pages can still render in isolation.

    Returns:
        User dict from session or empty dict if no session
    """
    try:
        user = get_current_user()
    except RuntimeError:
        return {}
    return user if isinstance(user, Mapping) else {}


def get_auth_headers(user: Mapping[str, Any]) -> dict[str, str]:
    """Build X-User-* headers for API requests.

    Args:
        user: User dict from session (must have role, user_id)

    Returns:
        Headers dict with X-User-Role, X-User-Id, X-User-Strategies
    """
    headers: dict[str, str] = {}
    role = user.get("role")
    user_id = user.get("user_id")
    strategies = get_authorized_strategies(user)

    if role:
        headers["X-User-Role"] = str(role)
    if user_id:
        headers["X-User-Id"] = str(user_id)
    if strategies:
        headers["X-User-Strategies"] = ",".join(sorted(strategies))

    return headers


def fetch_api(
    endpoint: str,
    user: Mapping[str, Any],
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fetch from API endpoint with auth headers.

    Args:
        endpoint: Key from ENDPOINTS dict
        user: User dict from session
        params: Optional query parameters

    Returns:
        JSON response as dict

    Raises:
        requests.RequestException: On network/HTTP errors
        requests.HTTPError: On 4xx/5xx responses
        ValueError: On JSON decode failure
        KeyError: If endpoint not found in ENDPOINTS
    """
    if endpoint not in ENDPOINTS:
        raise KeyError(f"Unknown endpoint: {endpoint}")

    url = ENDPOINTS[endpoint]
    headers = get_auth_headers(user)

    response = requests.get(url, params=params, headers=headers, timeout=API_REQUEST_TIMEOUT)
    response.raise_for_status()

    try:
        return cast(dict[str, Any], response.json())
    except ValueError as e:
        raise ValueError(f"Invalid JSON response from {endpoint}: {e}") from e


__all__ = [
    "safe_current_user",
    "get_auth_headers",
    "fetch_api",
]
