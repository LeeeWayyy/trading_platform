"""RBAC roles and permission helpers for the web console.

Default‑deny: any unknown role or missing mapping returns False.
Designed for lightweight use in Streamlit as well as backend tasks.
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import Callable, Iterable
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class Role(str, Enum):
    """Supported RBAC roles."""

    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"


class Permission(str, Enum):
    """Discrete permissions enforced by the web console."""

    VIEW_POSITIONS = "view_positions"
    VIEW_PNL = "view_pnl"
    VIEW_TRADES = "view_trades"
    CANCEL_ORDER = "cancel_order"
    CLOSE_POSITION = "close_position"
    FLATTEN_ALL = "flatten_all"
    MANAGE_USERS = "manage_users"
    VIEW_ALL_STRATEGIES = "view_all_strategies"
    EXPORT_DATA = "export_data"


ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.VIEWER: {
        Permission.VIEW_POSITIONS,
        Permission.VIEW_PNL,
        Permission.VIEW_TRADES,
    },
    Role.OPERATOR: {
        Permission.VIEW_POSITIONS,
        Permission.VIEW_PNL,
        Permission.VIEW_TRADES,
        Permission.CANCEL_ORDER,
        Permission.CLOSE_POSITION,
        Permission.FLATTEN_ALL,
        Permission.EXPORT_DATA,
    },
    Role.ADMIN: set(Permission),
}


def _normalize_role(role_value: Any) -> Role | None:
    """Convert arbitrary role value to Role enum or None if unknown."""

    if isinstance(role_value, Role):
        return role_value
    if isinstance(role_value, str):
        try:
            return Role(role_value)
        except ValueError:
            return None
    return None


def _extract_role(user_or_role: Any) -> Role | None:
    """Extract Role from Role/str/dict inputs."""

    if isinstance(user_or_role, dict):
        return _normalize_role(user_or_role.get("role"))

    role_attr = getattr(user_or_role, "role", None)
    if role_attr is not None:
        return _normalize_role(role_attr)

    return _normalize_role(user_or_role)


def has_permission(user_or_role: Any, permission: Permission) -> bool:
    """Check if role grants a permission (default‑deny on unknown)."""

    role = _extract_role(user_or_role)
    if role is None:
        return False

    if role is Role.ADMIN:
        return True

    return permission in ROLE_PERMISSIONS.get(role, set())


def require_permission(permission: Permission) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator enforcing the given permission.

    Looks for a ``user`` or ``session`` kwarg, or first positional argument
    that is a mapping with ``role``. Default‑deny if none found.
    Supports both sync and async callables.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        is_coroutine = asyncio.iscoroutinefunction(func)

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            subject = kwargs.get("user") or kwargs.get("session")
            if subject is None and args:
                subject = args[0]

            if not has_permission(subject, permission):
                logger.warning(
                    "permission_denied",
                    extra={
                        "permission": permission.value,
                        "role": getattr(subject, "role", None)
                        if not isinstance(subject, dict)
                        else subject.get("role"),
                    },
                )
                raise PermissionError(f"Permission '{permission.value}' required")

            return await func(*args, **kwargs)

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            subject = kwargs.get("user") or kwargs.get("session")
            if subject is None and args:
                subject = args[0]

            if not has_permission(subject, permission):
                logger.warning(
                    "permission_denied",
                    extra={
                        "permission": permission.value,
                        "role": getattr(subject, "role", None)
                        if not isinstance(subject, dict)
                        else subject.get("role"),
                    },
                )
                raise PermissionError(f"Permission '{permission.value}' required")

            return func(*args, **kwargs)

        return async_wrapper if is_coroutine else sync_wrapper

    return decorator


def get_authorized_strategies(user: dict[str, Any] | None) -> list[str]:
    """Return list of strategies user may access (default‑deny).

    Admins (with VIEW_ALL_STRATEGIES) are expected to receive the full list of
    strategy IDs from provisioning; this function simply returns the provided
    strategies list if present. Unknown roles or missing strategies return an
    empty list to fail closed.
    """

    if not user:
        return []

    role = _extract_role(user)
    strategies: Iterable[str] = user.get("strategies", []) if isinstance(user, dict) else []

    if role is None:
        return []

    return list(strategies)


__all__ = [
    "Role",
    "Permission",
    "ROLE_PERMISSIONS",
    "has_permission",
    "require_permission",
    "get_authorized_strategies",
]
