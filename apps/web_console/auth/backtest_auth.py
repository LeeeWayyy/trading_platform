"""Auth decorator with dev-mode fallback for T5.3 Backtest UI.

This module provides a temporary auth stub for development while T6.1 (OAuth2) is pending.
The stub is controlled by BACKTEST_DEV_AUTH environment variable.

SECURITY:
- NEVER enable BACKTEST_DEV_AUTH=true in production/staging
- CI governance tests (test_auth_governance.py) enforce this
- After T6.1 ships, this file should be deleted per rollback path

Rollback Path (when T6.1 ships):
1. Remove BACKTEST_DEV_AUTH from all env files
2. Replace @backtest_requires_auth with @requires_auth in pages/backtest.py
3. Delete this file
4. Run test_no_auth_stub_references_after_t61 to verify cleanup
"""

from __future__ import annotations

import functools
import os
from collections.abc import Callable
from typing import Any

import streamlit as st

from apps.web_console.auth.streamlit_helpers import requires_auth


def backtest_requires_auth(func: Callable[..., Any]) -> Callable[..., Any]:
    """Auth decorator with dev-mode fallback for T5.3.

    CRITICAL: Dev stub must set the same session keys as real OAuth2 auth:
    - authenticated, username, user_id, auth_method, session_id
    - role, strategies (for RBAC parity)

    This ensures get_user_info() and RBAC checks work correctly in both modes.

    Args:
        func: The page render function to protect

    Returns:
        Wrapped function with auth check
    """
    if os.getenv("BACKTEST_DEV_AUTH", "false").lower() in ("true", "1", "yes", "on"):
        # Dev mode: set stub user with same session shape as OAuth2
        # CRITICAL: Must include role and strategies for RBAC parity
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            st.session_state["authenticated"] = True
            st.session_state["username"] = "dev_user"
            st.session_state["user_id"] = "dev_user_id"
            st.session_state["auth_method"] = "dev_stub"
            st.session_state["session_id"] = "dev_session"
            st.session_state["role"] = "operator"  # RBAC role for permission checks
            st.session_state["strategies"] = ["*"]  # Access to all strategies
            return func(*args, **kwargs)

        return wrapper
    else:
        # Production: use real auth
        return requires_auth(func)


__all__ = ["backtest_requires_auth"]
