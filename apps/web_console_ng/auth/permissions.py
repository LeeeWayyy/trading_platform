from enum import Enum


class Role(str, Enum):
    ADMIN = "admin"
    TRADER = "trader"
    VIEWER = "viewer"

class Permission(str, Enum):
    VIEW_DASHBOARD = "view_dashboard"
    EXECUTE_TRADES = "execute_trades"
    MANAGE_USERS = "manage_users"
    VIEW_AUDIT_LOGS = "view_audit_logs"

ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": {
        Permission.VIEW_DASHBOARD,
        Permission.EXECUTE_TRADES,
        Permission.MANAGE_USERS,
        Permission.VIEW_AUDIT_LOGS,
    },
    "trader": {
        Permission.VIEW_DASHBOARD,
        Permission.EXECUTE_TRADES,
    },
    "viewer": {
        Permission.VIEW_DASHBOARD,
    },
}
