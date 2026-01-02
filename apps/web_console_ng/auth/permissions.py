"""Role and permission re-exports from shared auth library.

This module re-exports the canonical Role and Permission enums from
libs.web_console_auth.permissions to ensure consistent role taxonomy
across all services.

IMPORTANT: Always use these imports rather than defining local roles.
The shared library defines: VIEWER, RESEARCHER, OPERATOR, ADMIN.
"""

from libs.web_console_auth.permissions import (
    ROLE_DATASET_PERMISSIONS,
    ROLE_PERMISSIONS,
    DatasetPermission,
    Permission,
    Role,
    get_authorized_strategies,
    has_dataset_permission,
    has_permission,
    require_permission,
)

__all__ = [
    "Role",
    "Permission",
    "DatasetPermission",
    "ROLE_PERMISSIONS",
    "ROLE_DATASET_PERMISSIONS",
    "has_permission",
    "has_dataset_permission",
    "require_permission",
    "get_authorized_strategies",
]
