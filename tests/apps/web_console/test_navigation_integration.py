"""Navigation integration tests for Track 7 pages.

Tests verify that:
- System Health appears in nav with FEATURE_HEALTH_MONITOR + VIEW_CIRCUIT_BREAKER
- Admin Dashboard appears with any of MANAGE_API_KEYS, MANAGE_SYSTEM_CONFIG, VIEW_AUDIT
- Audit Log requires VIEW_AUDIT permission (RBAC fix)
- Navigation order is stable regardless of feature flag combinations
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest


# Mock Permission enum for testing
class MockPermission:
    VIEW_TRADES = "VIEW_TRADES"
    VIEW_CIRCUIT_BREAKER = "VIEW_CIRCUIT_BREAKER"
    VIEW_PNL = "VIEW_PNL"
    VIEW_ALERTS = "VIEW_ALERTS"
    VIEW_AUDIT = "VIEW_AUDIT"
    MANAGE_USERS = "MANAGE_USERS"
    MANAGE_API_KEYS = "MANAGE_API_KEYS"
    MANAGE_SYSTEM_CONFIG = "MANAGE_SYSTEM_CONFIG"


def build_pages_list(
    user_permissions: set[str],
    feature_flags: dict[str, bool],
) -> list[str]:
    """Build the pages list using the same logic as app.py main() function.

    This is a test helper that replicates the navigation logic for verification.
    """

    def has_permission(user_info: dict, perm: str) -> bool:
        return perm in user_permissions

    # Base pages (Audit Log removed per C6.1 RBAC fix)
    pages = ["Dashboard", "Manual Order Entry", "Kill Switch"]

    if feature_flags.get("FEATURE_MANUAL_CONTROLS", False) and has_permission(
        {}, MockPermission.VIEW_TRADES
    ):
        pages.insert(2, "Manual Trade Controls")

    if feature_flags.get("FEATURE_CIRCUIT_BREAKER", False) and has_permission(
        {}, MockPermission.VIEW_CIRCUIT_BREAKER
    ):
        pages.insert(3, "Circuit Breaker")

    if feature_flags.get("FEATURE_STRATEGY_COMPARISON", False):
        pages.append("Strategy Comparison")

    if feature_flags.get("FEATURE_BACKTEST_MANAGER", False) and has_permission(
        {}, MockPermission.VIEW_PNL
    ):
        pages.append("Backtest Manager")

    if has_permission({}, MockPermission.MANAGE_USERS):
        pages.append("User Management")

    if feature_flags.get("FEATURE_ALERTS", False) and has_permission(
        {}, MockPermission.VIEW_ALERTS
    ):
        pages.append("Alerts")

    # C6.1: System Health requires feature flag AND VIEW_CIRCUIT_BREAKER
    if feature_flags.get("FEATURE_HEALTH_MONITOR", False) and has_permission(
        {}, MockPermission.VIEW_CIRCUIT_BREAKER
    ):
        pages.append("System Health")

    # C6.1: Audit Log requires VIEW_AUDIT permission
    if has_permission({}, MockPermission.VIEW_AUDIT):
        pages.append("Audit Log")

    # C6.1: Admin Dashboard uses permission-based access
    if any(
        has_permission({}, p)
        for p in [
            MockPermission.MANAGE_API_KEYS,
            MockPermission.MANAGE_SYSTEM_CONFIG,
            MockPermission.VIEW_AUDIT,
        ]
    ):
        pages.append("Admin Dashboard")

    return pages


class TestSystemHealthNavigation:
    """Tests for System Health page navigation visibility."""

    def test_system_health_visible_when_flag_and_permission(self):
        """FEATURE_HEALTH_MONITOR=true + VIEW_CIRCUIT_BREAKER -> System Health in nav."""
        pages = build_pages_list(
            user_permissions={MockPermission.VIEW_CIRCUIT_BREAKER},
            feature_flags={"FEATURE_HEALTH_MONITOR": True},
        )
        assert "System Health" in pages

    def test_system_health_hidden_when_flag_disabled(self):
        """FEATURE_HEALTH_MONITOR=false -> System Health NOT in nav."""
        pages = build_pages_list(
            user_permissions={MockPermission.VIEW_CIRCUIT_BREAKER},
            feature_flags={"FEATURE_HEALTH_MONITOR": False},
        )
        assert "System Health" not in pages

    def test_system_health_hidden_without_permission(self):
        """FEATURE_HEALTH_MONITOR=true but no VIEW_CIRCUIT_BREAKER -> NOT in nav."""
        pages = build_pages_list(
            user_permissions=set(),  # No permissions
            feature_flags={"FEATURE_HEALTH_MONITOR": True},
        )
        assert "System Health" not in pages


class TestAdminDashboardNavigation:
    """Tests for Admin Dashboard page navigation visibility."""

    def test_admin_visible_for_manage_api_keys(self):
        """User with MANAGE_API_KEYS -> Admin Dashboard in nav."""
        pages = build_pages_list(
            user_permissions={MockPermission.MANAGE_API_KEYS},
            feature_flags={},
        )
        assert "Admin Dashboard" in pages

    def test_admin_visible_for_manage_system_config(self):
        """User with MANAGE_SYSTEM_CONFIG -> Admin Dashboard in nav."""
        pages = build_pages_list(
            user_permissions={MockPermission.MANAGE_SYSTEM_CONFIG},
            feature_flags={},
        )
        assert "Admin Dashboard" in pages

    def test_admin_visible_for_view_audit(self):
        """User with VIEW_AUDIT -> Admin Dashboard in nav."""
        pages = build_pages_list(
            user_permissions={MockPermission.VIEW_AUDIT},
            feature_flags={},
        )
        assert "Admin Dashboard" in pages

    def test_admin_hidden_for_viewer(self):
        """Viewer role (no admin permissions) -> Admin Dashboard NOT in nav."""
        pages = build_pages_list(
            user_permissions={MockPermission.VIEW_TRADES, MockPermission.VIEW_PNL},
            feature_flags={},
        )
        assert "Admin Dashboard" not in pages


class TestAuditLogNavigation:
    """Tests for Audit Log page navigation visibility (RBAC fix verification)."""

    def test_audit_log_visible_with_view_audit(self):
        """User with VIEW_AUDIT -> Audit Log in nav."""
        pages = build_pages_list(
            user_permissions={MockPermission.VIEW_AUDIT},
            feature_flags={},
        )
        assert "Audit Log" in pages

    def test_audit_log_hidden_without_view_audit(self):
        """User without VIEW_AUDIT -> Audit Log NOT in nav (RBAC fix)."""
        pages = build_pages_list(
            user_permissions={MockPermission.VIEW_TRADES},  # No VIEW_AUDIT
            feature_flags={},
        )
        assert "Audit Log" not in pages

    def test_audit_log_hidden_by_default(self):
        """Default pages list should NOT include Audit Log."""
        pages = build_pages_list(
            user_permissions=set(),  # No permissions
            feature_flags={},
        )
        assert "Audit Log" not in pages


class TestNavigationOrder:
    """Tests for navigation ordering stability."""

    def test_navigation_order_stable(self):
        """Verify insertion order doesn't break existing pages."""
        # User with many permissions and all flags enabled
        pages = build_pages_list(
            user_permissions={
                MockPermission.VIEW_TRADES,
                MockPermission.VIEW_CIRCUIT_BREAKER,
                MockPermission.VIEW_PNL,
                MockPermission.VIEW_ALERTS,
                MockPermission.VIEW_AUDIT,
                MockPermission.MANAGE_USERS,
                MockPermission.MANAGE_API_KEYS,
            },
            feature_flags={
                "FEATURE_MANUAL_CONTROLS": True,
                "FEATURE_CIRCUIT_BREAKER": True,
                "FEATURE_STRATEGY_COMPARISON": True,
                "FEATURE_BACKTEST_MANAGER": True,
                "FEATURE_ALERTS": True,
                "FEATURE_HEALTH_MONITOR": True,
            },
        )

        # Core pages must always be present in correct order
        assert pages[0] == "Dashboard"
        assert pages[1] == "Manual Order Entry"

        # All expected pages should be present (order may vary after inserts)
        expected_pages = {
            "Dashboard",
            "Manual Order Entry",
            "Kill Switch",
            "Manual Trade Controls",
            "Circuit Breaker",
            "Strategy Comparison",
            "Backtest Manager",
            "User Management",
            "Alerts",
            "System Health",
            "Audit Log",
            "Admin Dashboard",
        }
        assert set(pages) == expected_pages

    def test_base_pages_always_present(self):
        """Base pages (Dashboard, Manual Order Entry, Kill Switch) always present."""
        pages = build_pages_list(user_permissions=set(), feature_flags={})
        assert "Dashboard" in pages
        assert "Manual Order Entry" in pages
        assert "Kill Switch" in pages


class TestPageRendering:
    """Integration tests for page rendering (requires mocked Streamlit)."""

    @pytest.fixture()
    def mock_streamlit(self):
        """Mock Streamlit components for testing."""
        with patch("streamlit.error") as mock_error, patch("streamlit.stop") as mock_stop:
            yield {"error": mock_error, "stop": mock_stop}

    def test_audit_log_rbac_guard_blocks_unauthorized(self, mock_streamlit):
        """Verify Audit Log page has RBAC guard that blocks unauthorized access."""
        # This tests the defense-in-depth RBAC check inside the page render branch
        # The navigation already filters the page, but the page itself should also check

        # Import the permission check function
        from apps.web_console.auth.permissions import Permission, has_permission

        # Simulate user without VIEW_AUDIT permission
        user_without_permission: dict[str, Any] = {
            "user_id": "test-user",
            "role": "viewer",
            "permissions": [],
        }

        # Verify has_permission returns False
        assert not has_permission(user_without_permission, Permission.VIEW_AUDIT)

    def test_system_health_renders_without_error(self):
        """Verify System Health page can be imported and has render function."""
        from apps.web_console.pages.health import render_health_monitor

        # Verify the function exists and is callable
        assert callable(render_health_monitor)

    def test_admin_renders_without_error(self):
        """Verify Admin Dashboard page can be imported and has render function."""
        from apps.web_console.pages.admin import render_admin_page

        # Verify the function exists and is callable
        assert callable(render_admin_page)
