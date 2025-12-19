"""Auth decorator with dev-mode fallback for Track 7 Operations.

T6.1 (Auth/RBAC) is DELIVERED in PR#76 (merged 2025-12-12). This stub provides
local development convenience - in production it delegates to the real @requires_auth.

The stub is controlled by OPERATIONS_DEV_AUTH environment variable.

SECURITY:
- NEVER enable OPERATIONS_DEV_AUTH=true in production/staging
- Runtime guard blocks app startup if violated (fail-closed allowlist)
- CI governance tests (test_operations_auth_governance.py) enforce this

Optional Removal Path (if dev stub no longer needed):
1. Remove OPERATIONS_DEV_AUTH from all env files
2. Replace @operations_requires_auth with @requires_auth in pages
3. Delete this file
"""

from __future__ import annotations

import functools
import os
import sys
from collections.abc import Callable
from typing import Any

import streamlit as st

from apps.web_console.auth.streamlit_helpers import requires_auth

# Allowlist: ONLY these environments can use dev auth (fail-closed security)
_ALLOWED_DEV_AUTH_ENVIRONMENTS = frozenset({
    "development",
    "dev",
    "local",
    "test",
    "ci",
})

# Module-level constant: computed once at import time (DRY)
_DEV_AUTH_ENABLED = os.getenv("OPERATIONS_DEV_AUTH", "false").lower() in ("true", "1", "yes", "on")


def _check_dev_auth_safety() -> None:
    """Runtime guard: refuse to start if dev auth enabled outside allowed environments.

    SECURITY: Uses allowlist (fail-closed) - if ENVIRONMENT is unset, mistyped, or
    unknown, dev auth is blocked. Only explicitly allowed environments can use it.
    """
    if _DEV_AUTH_ENABLED:
        env = os.getenv("ENVIRONMENT", "").lower()  # Empty string if unset
        if env not in _ALLOWED_DEV_AUTH_ENVIRONMENTS:
            print(
                f"FATAL: OPERATIONS_DEV_AUTH=true is only allowed in "
                f"{sorted(_ALLOWED_DEV_AUTH_ENVIRONMENTS)}. "
                f"Current ENVIRONMENT='{env or '(unset)'}'. "
                "Remove OPERATIONS_DEV_AUTH or set ENVIRONMENT to an allowed value.",
                file=sys.stderr,
            )
            sys.exit(1)


# Run check at module import time
_check_dev_auth_safety()


def operations_requires_auth(func: Callable[..., Any]) -> Callable[..., Any]:
    """Auth decorator with dev-mode fallback for Track 7 operations.

    In production (OPERATIONS_DEV_AUTH not set), delegates to T6.1's @requires_auth.
    In dev mode, provides stub that mimics T6.1's session shape for local testing.

    CRITICAL: Dev stub must set the same session keys as real OAuth2 auth:
    - authenticated, username, user_id, auth_method, session_id
    - role, strategies (for RBAC parity)

    Uses admin role for full operations access (CB trip/reset, user management, etc.)

    Args:
        func: The page render function to protect

    Returns:
        Wrapped function with auth check
    """
    if _DEV_AUTH_ENABLED:
        # Dev mode: set stub user with same session shape as OAuth2
        # CRITICAL: Must include role and strategies for RBAC parity
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            st.session_state["authenticated"] = True
            st.session_state["username"] = "dev_user"
            st.session_state["user_id"] = "dev_user_id"
            st.session_state["auth_method"] = "dev_stub"
            st.session_state["session_id"] = "dev_session"
            st.session_state["role"] = "admin"  # Admin for full operations access
            st.session_state["strategies"] = ["*"]  # Access to all strategies
            return func(*args, **kwargs)

        return wrapper
    else:
        # Production: use real auth
        return requires_auth(func)


__all__ = ["operations_requires_auth"]
