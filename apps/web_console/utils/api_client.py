"""Shared API client and helpers for web console pages.

This module centralizes common API-related utilities used across pages:
- safe_current_user(): Safely get current user from session
- get_auth_headers(): Build X-User-* headers for API requests
- fetch_api(): Fetch from API endpoint with auth headers (legacy pages)
- Manual controls helpers for service-token authenticated calls
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Mapping
from functools import lru_cache
from typing import Any, cast

import redis
import requests

from apps.web_console.auth.permissions import get_authorized_strategies
from apps.web_console.auth.session_manager import get_current_user
from apps.web_console.config import (
    API_REQUEST_TIMEOUT,
    ENDPOINTS,
    MANUAL_CONTROLS_API_BASE,
)
from libs.web_console_auth.config import AuthConfig
from libs.web_console_auth.jwt_manager import JWTManager


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
    "generate_service_token_for_user",
    "get_manual_controls_headers",
    "get_manual_controls_api",
    "post_manual_controls_api",
    "ManualControlsAPIError",
]


# ============================================================================
# Manual Controls API Helpers (T6.6)
# ============================================================================


class ManualControlsAPIError(Exception):
    """Custom exception for manual controls API failures."""

    def __init__(
        self,
        status_code: int,
        error_code: str,
        message: str,
        detail: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.message = message
        self.detail = detail


@lru_cache(maxsize=None)  # noqa: UP033 - explicit lru_cache requested for singleton behavior
def _get_jwt_manager() -> JWTManager:
    """Return singleton JWTManager for service token generation."""

    config = AuthConfig.from_env()
    redis_client = redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        db=0,
        decode_responses=True,
    )
    return JWTManager(config=config, redis_client=redis_client)


def generate_service_token_for_user(user: Mapping[str, Any]) -> str:
    """Generate service JWT for backend API calls on behalf of user.

    Raises:
        ValueError: If user_id/sub is missing or empty (fail-closed)
    """
    manager = _get_jwt_manager()
    user_id = user.get("user_id") or user.get("sub")

    # Fail-closed: Validate identity before minting token
    if not user_id:
        raise ValueError("User identity missing (user_id/sub) - cannot mint service token")

    session_id = user.get("session_id", str(uuid.uuid4()))
    client_ip = user.get("ip", "0.0.0.0")
    user_agent = user.get("user_agent", "web-console")

    return manager.generate_service_token(
        user_id=str(user_id),
        session_id=session_id,
        client_ip=client_ip,
        user_agent=user_agent,
    )


def get_manual_controls_headers(user: Mapping[str, Any]) -> dict[str, str]:
    """Build headers for manual controls API calls.

    session_version MUST come from user session; fail closed if missing.
    """

    token = generate_service_token_for_user(user)
    user_id = user.get("user_id") or user.get("sub")

    session_version = user.get("session_version")
    if session_version is None:
        raise ValueError("User session missing session_version - cannot call backend API")

    return {
        "Authorization": f"Bearer {token}",
        "X-User-ID": str(user_id),
        "X-Request-ID": str(uuid.uuid4()),
        "X-Session-Version": str(session_version),
        "Content-Type": "application/json",
    }


def _handle_manual_controls_error(response: requests.Response) -> None:
    """Raise ManualControlsAPIError with parsed payload."""

    status = response.status_code
    payload: Any | None = None
    try:
        payload = response.json()
    except ValueError:
        payload = None

    detail = payload.get("detail") if isinstance(payload, Mapping) else payload

    # FastAPI validation errors (422) return list of errors in detail
    if status == 422:
        raise ManualControlsAPIError(
            status_code=status,
            error_code="validation_error",
            message="Validation error",
            detail=detail,
        )

    # Status-code fallback mapping when backend returns plain string or no error code
    # Ensures proper UI handling (re-login prompts, rate-limit messaging, etc.)
    STATUS_CODE_FALLBACK = {
        400: "invalid_request",
        401: "token_expired",
        403: "permission_denied",
        404: "not_found",
        429: "rate_limited",
        500: "internal_error",
        502: "broker_unavailable",
        503: "broker_unavailable",
        504: "broker_timeout",
    }

    error_code = "unknown_error"
    message = response.reason or "Request failed"

    if isinstance(detail, Mapping):
        error_code = detail.get("error") or detail.get("code") or STATUS_CODE_FALLBACK.get(status, error_code)
        message = detail.get("message") or message
        retry_after = detail.get("retry_after") or response.headers.get("Retry-After")
        if retry_after is not None:
            detail = dict(detail)
            detail["retry_after"] = retry_after
    elif isinstance(detail, list) and detail:
        # Unexpected list detail (non-422)
        message = detail[0].get("msg", message) if isinstance(detail[0], Mapping) else message
        error_code = STATUS_CODE_FALLBACK.get(status, error_code)
    else:
        # Plain string or no detail - use status code fallback
        error_code = STATUS_CODE_FALLBACK.get(status, error_code)
        if isinstance(detail, str) and detail:
            message = detail

    raise ManualControlsAPIError(
        status_code=status,
        error_code=str(error_code),
        message=str(message),
        detail=detail,
    )


def _build_manual_controls_url(path: str) -> str:
    """Ensure path is joined with manual controls API base."""

    if not path.startswith("/"):
        path = "/" + path
    return f"{MANUAL_CONTROLS_API_BASE}{path}"


def get_manual_controls_api(
    path: str,
    user: Mapping[str, Any],
    params: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Issue GET to manual controls API with service JWT and headers."""

    headers = get_manual_controls_headers(user)
    url = _build_manual_controls_url(path)

    response = requests.get(url, params=params, headers=headers, timeout=API_REQUEST_TIMEOUT)
    if response.status_code >= 400:
        _handle_manual_controls_error(response)
    return cast(dict[str, Any], response.json())


def post_manual_controls_api(
    path: str,
    user: Mapping[str, Any],
    json_body: Mapping[str, Any],
) -> dict[str, Any]:
    """Issue POST to manual controls API with service JWT and headers."""

    headers = get_manual_controls_headers(user)
    url = _build_manual_controls_url(path)

    response = requests.post(url, json=json_body, headers=headers, timeout=API_REQUEST_TIMEOUT)
    if response.status_code >= 400:
        _handle_manual_controls_error(response)
    return cast(dict[str, Any], response.json())
