from __future__ import annotations

from libs.platform.web_console_auth.permissions import ROLE_PERMISSIONS, Permission, Role


def test_p4t7_permissions_exist_in_enum() -> None:
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
    researcher_perms = ROLE_PERMISSIONS[Role.RESEARCHER]
    assert Permission.VIEW_ALPHA_SIGNALS in researcher_perms
    assert Permission.VIEW_FACTOR_ANALYTICS in researcher_perms
    assert Permission.LAUNCH_NOTEBOOKS in researcher_perms
    assert Permission.VIEW_REPORTS in researcher_perms
    assert Permission.VIEW_TAX_REPORTS in researcher_perms
    assert Permission.VIEW_ALL_POSITIONS not in researcher_perms

    assert Permission.VIEW_REPORTS in ROLE_PERMISSIONS[Role.OPERATOR]
    assert Permission.VIEW_REPORTS in ROLE_PERMISSIONS[Role.VIEWER]

    admin_perms = ROLE_PERMISSIONS[Role.ADMIN]
    assert Permission.VIEW_ALL_POSITIONS in admin_perms
    assert Permission.MANAGE_REPORTS in admin_perms
    assert Permission.MANAGE_TAX_SETTINGS in admin_perms


def test_view_all_positions_admin_only() -> None:
    for role in (Role.VIEWER, Role.OPERATOR, Role.RESEARCHER):
        assert Permission.VIEW_ALL_POSITIONS not in ROLE_PERMISSIONS[role]
    assert Permission.VIEW_ALL_POSITIONS in ROLE_PERMISSIONS[Role.ADMIN]
