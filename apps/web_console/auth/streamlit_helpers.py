"""Streamlit authentication helpers for protected pages.

Provides decorators and utilities for OAuth2 session validation.
"""

import functools
import logging
from collections.abc import Callable
from typing import Any

import streamlit as st

from apps.web_console.auth import check_password

logger = logging.getLogger(__name__)


def requires_auth(func: Callable[..., Any]) -> Callable[..., Any]:
    """Decorator for pages requiring OAuth2 authentication.

    Usage:
        @requires_auth
        def main():
            st.title("Protected Dashboard")
            ...

    If user is not authenticated, redirects to login page via check_password().
    """

    @functools.wraps(func)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        # Check authentication (handles redirect to login if needed)
        if not check_password():
            st.stop()
            return None

        # User authenticated - proceed with page rendering
        return func(*args, **kwargs)

    return wrapper


def get_user_info() -> dict[str, str]:
    """Get current authenticated user information.

    Returns:
        dict with keys: username, email, user_id, auth_method, session_id

    Raises:
        RuntimeError: If called before authentication
    """
    if not st.session_state.get("authenticated", False):
        raise RuntimeError("get_user_info() called before authentication")

    return {
        "username": st.session_state.get("username", "unknown"),
        "email": st.session_state.get("username", "unknown"),  # Username is email for OAuth2
        "user_id": st.session_state.get("user_id", "unknown"),
        "auth_method": st.session_state.get("auth_method", "unknown"),
        "session_id": st.session_state.get("session_id", "unknown"),
    }
