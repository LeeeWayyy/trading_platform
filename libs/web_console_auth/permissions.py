"""RBAC roles and permission helpers shared across services.

Default‑deny: any unknown role or missing mapping returns False.
Designed for lightweight use in Streamlit as well as backend services.
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
    ADJUST_POSITION = "adjust_position"
    FLATTEN_ALL = "flatten_all"
    MANAGE_USERS = "manage_users"
    MANAGE_STRATEGIES = "manage_strategies"  # [v1.5] Strategy configuration
    MANAGE_RECONCILIATION = "manage_reconciliation"  # [v2.0] Reconciliation override
    VIEW_ALL_STRATEGIES = "view_all_strategies"
    VIEW_AUDIT = "view_audit"  # [v1.5] Audit log access
    EXPORT_DATA = "export_data"

    # Circuit Breaker permissions (T7.1)
    VIEW_CIRCUIT_BREAKER = "view_circuit_breaker"
    TRIP_CIRCUIT = "trip_circuit"
    RESET_CIRCUIT = "reset_circuit"


ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.VIEWER: {
        Permission.VIEW_POSITIONS,
        Permission.VIEW_PNL,
        Permission.VIEW_TRADES,
        Permission.VIEW_CIRCUIT_BREAKER,  # T7.1: Can view CB status
    },
    Role.OPERATOR: {
        Permission.VIEW_POSITIONS,
        Permission.VIEW_PNL,
        Permission.VIEW_TRADES,
        Permission.CANCEL_ORDER,
        Permission.CLOSE_POSITION,
        Permission.ADJUST_POSITION,
        Permission.FLATTEN_ALL,
        Permission.EXPORT_DATA,
        Permission.MANAGE_STRATEGIES,  # [v1.5] Operators can manage strategies
        Permission.VIEW_CIRCUIT_BREAKER,  # T7.1: Can view CB status
        Permission.TRIP_CIRCUIT,  # T7.1: Can manually trip CB
        Permission.RESET_CIRCUIT,  # T7.1: Can reset CB (with rate limit)
    },
    Role.ADMIN: set(Permission),  # Admins have all permissions including VIEW_AUDIT
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


def require_permission(
    permission: Permission,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorator enforcing the given permission.

    Looks for a ``user`` or ``session`` kwarg, or first positional argument
    that is a mapping with ``role``. If the first positional argument is a
    request-like object, a ``user`` or ``session`` attribute on that object
    will be used. Default‑deny if none found.
    Supports both sync and async callables. The decorated function must either
    expose a ``user`` or ``session`` keyword argument, or accept a first
    positional argument that is one of:
    - a mapping containing ``role``
    - an object with ``role`` attribute
    - a request-like object exposing ``user`` or ``session`` attributes.
    Calls that do not satisfy this contract will be denied.
    """

    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        is_coroutine = asyncio.iscoroutinefunction(func)

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            subject = kwargs.get("user") or kwargs.get("session")
            if subject is None and args:
                first_arg = args[0]
                # Common pattern: FastAPI/Starlette Request exposes .user
                subject = getattr(first_arg, "user", None)
                if subject is None:
                    subject = getattr(first_arg, "session", None)
                if subject is None:
                    subject = first_arg

            if not has_permission(subject, permission):
                if subject is None:
                    role_value = None
                elif isinstance(subject, dict):
                    role_value = subject.get("role")
                else:
                    role_value = getattr(subject, "role", None)
                logger.warning(
                    "permission_denied",
                    extra={"permission": permission.value, "role": role_value},
                )
                raise PermissionError(f"Permission '{permission.value}' required")

            return await func(*args, **kwargs)

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            subject = kwargs.get("user") or kwargs.get("session")
            if subject is None and args:
                first_arg = args[0]
                subject = getattr(first_arg, "user", None)
                if subject is None:
                    subject = getattr(first_arg, "session", None)
                if subject is None:
                    subject = first_arg

            if not has_permission(subject, permission):
                if subject is None:
                    role_value = None
                elif isinstance(subject, dict):
                    role_value = subject.get("role")
                else:
                    role_value = getattr(subject, "role", None)
                logger.warning(
                    "permission_denied",
                    extra={"permission": permission.value, "role": role_value},
                )
                raise PermissionError(f"Permission '{permission.value}' required")

            return func(*args, **kwargs)

        return async_wrapper if is_coroutine else sync_wrapper

    return decorator


def get_authorized_strategies(user: Any | None) -> list[str]:
    """Return list of strategies user may access (default‑deny).

    Admins (with VIEW_ALL_STRATEGIES) are expected to receive the full list of
    strategy IDs from provisioning. Callers without VIEW_ALL_STRATEGIES get
    only their explicitly assigned strategies. Unknown roles or missing
    strategies return an empty list to fail closed.
    """

    if not user:
        return []

    role = _extract_role(user)
    strategies: Iterable[str]
    if isinstance(user, dict):
        strategies = user.get("strategies", [])
    else:
        # Support ORM/user objects that expose a ``strategies`` attribute
        strategies = getattr(user, "strategies", []) or []

    if role is None:
        return []

    strategies_list = list(strategies)

    if not has_permission(user, Permission.VIEW_ALL_STRATEGIES):
        # Fail closed: only return explicitly assigned strategies
        return strategies_list

    return strategies_list


__all__ = [
    "Role",
    "Permission",
    "ROLE_PERMISSIONS",
    "has_permission",
    "require_permission",
    "get_authorized_strategies",
]
