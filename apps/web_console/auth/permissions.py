"""Proxy module for RBAC helpers.

Implementation lives in ``libs.web_console_auth.permissions`` so backend
services (e.g., execution_gateway) can depend on a shared library rather than
frontend code. This module re-exports the same symbols for backward
compatibility with existing imports in the web console.
"""

from libs.web_console_auth.permissions import (  # noqa:F401
    ROLE_PERMISSIONS,
    Permission,
    Role,
    get_authorized_strategies,
    has_permission,
    require_permission,
)

__all__ = [
    "Role",
    "Permission",
    "ROLE_PERMISSIONS",
    "has_permission",
    "require_permission",
    "get_authorized_strategies",
]
