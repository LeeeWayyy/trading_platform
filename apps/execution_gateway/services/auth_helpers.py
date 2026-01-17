"""Authentication and authorization helper functions.

This module provides RBAC (Role-Based Access Control) helpers extracted from main.py,
enabling clean authentication context building and user validation.

Design Rationale:
    - Pure function for easy unit testing
    - Fail-closed security (require explicit authentication)
    - Extract user context from request.state (set by middleware)
    - Support both dict and object-based user representations

Usage:
    from apps.execution_gateway.services.auth_helpers import build_user_context

    @router.get("/api/v1/performance")
    async def get_performance(request: Request):
        user_ctx = build_user_context(request)
        strategies = user_ctx["strategies"]
        # Use strategies for scoped queries

See REFACTOR_EXECUTION_GATEWAY_TASK.md Phase 1 for design decisions.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request, status


def build_user_context(request: Request) -> dict[str, Any]:
    """Extract user context for RBAC.

    Fail closed when no authenticated user is attached to the request. Upstream
    middleware must populate ``request.state.user`` with a trusted object or
    mapping that includes a role (and optionally strategies and user_id).
    Client-provided headers are intentionally ignored to avoid spoofing.

    Args:
        request: FastAPI request with authenticated user in state

    Returns:
        Dictionary containing:
            - role: User's role (e.g., "admin", "trader")
            - strategies: List of strategy IDs user has access to
            - requested_strategies: Strategies from query params
            - user_id: User's unique identifier
            - user: Full user object/dict

    Raises:
        HTTPException (401): If user not authenticated or missing role

    Security Notes:
        - Fails closed: missing user → 401
        - Trusts only request.state.user (set by middleware)
        - Ignores client headers to prevent spoofing
        - Requires role field (empty role → 401)

    Example:
        >>> # Middleware sets request.state.user
        >>> user_ctx = build_user_context(request)
        >>> if user_ctx["role"] == "admin":
        ...     # Allow admin access
        ...     pass
        >>> strategies = user_ctx["strategies"]  # User's accessible strategies
    """
    state_user = getattr(request.state, "user", None)

    if state_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authenticated user context",
        )

    if isinstance(state_user, dict):
        user: dict[str, Any] = dict(state_user)
    else:
        user = dict(getattr(state_user, "__dict__", {}))
        # Preserve common attributes even if __dict__ is empty/filtered
        for attr in ("role", "strategies", "user_id", "id"):
            value = getattr(state_user, attr, None)
            if value is not None:
                user.setdefault("user_id" if attr == "id" else attr, value)

    if not user.get("role"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authenticated user context",
        )

    requested_strategies = request.query_params.getlist("strategies")

    return {
        "role": user.get("role"),
        "strategies": user.get("strategies", []),
        "requested_strategies": requested_strategies,
        "user_id": user.get("user_id"),
        "user": user,
    }
