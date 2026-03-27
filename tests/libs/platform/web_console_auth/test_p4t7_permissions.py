from __future__ import annotations

from libs.platform.web_console_auth.permissions import (
    ROLE_PERMISSIONS,
    Permission,
    Role,
    is_admin,
)


def test_p4t7_permissions_exist_in_enum() -> None:
    """Permission enum members still exist for backward compatibility."""
    expected = {
        "view_alpha_signals": Permission.VIEW_ALPHA_SIGNALS,
        "view_factor_analytics": Permission.VIEW_FACTOR_ANALYTICS,
        "view_all_positions": Permission.VIEW_ALL_POSITIONS,
        "launch_notebooks": Permission.LAUNCH_NOTEBOOKS,
        "manage_notebooks": Permission.MANAGE_NOTEBOOKS,
        "manage_reports": Permission.MANAGE_REPORTS,
        "view_reports": Permission.VIEW_REPORTS,
        "view_tax_reports": Permission.VIEW_TAX_REPORTS,
        "manage_tax_settings": Permission.MANAGE_TAX_SETTINGS,
    }

    for value, enum_member in expected.items():
        assert enum_member.value == value


def test_role_permissions_include_p4t7_mappings() -> None:
    """P6T19: ROLE_PERMISSIONS is now an empty dict (single-admin model)."""
    assert ROLE_PERMISSIONS == {}


def test_view_all_positions_admin_only() -> None:
    """P6T19: is_admin returns True for any input (single-admin model)."""
    assert is_admin(Role.VIEWER) is True
    assert is_admin(Role.OPERATOR) is True
    assert is_admin(Role.ADMIN) is True
    assert is_admin(None) is True
    assert is_admin("unknown") is True
