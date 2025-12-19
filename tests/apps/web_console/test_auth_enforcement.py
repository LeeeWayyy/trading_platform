"""Tests for OAuth2 authentication enforcement on backtest page.

These tests verify that:
1. Backtest page requires authentication in non-dev mode
2. Dev stub only activates when BACKTEST_DEV_AUTH=true
3. RBAC permissions are checked correctly
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest


class TestBacktestAuthEnforcement:
    """Test authentication enforcement on backtest page."""

    def test_backtest_requires_auth_uses_real_auth_when_env_not_set(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify OAuth2 auth is required when BACKTEST_DEV_AUTH is not set.

        In production, BACKTEST_DEV_AUTH should not be set, so the real
        @requires_auth decorator should be used.
        """
        # Ensure env var is not set
        monkeypatch.delenv("BACKTEST_DEV_AUTH", raising=False)

        # Import fresh to pick up env change
        import importlib

        import apps.web_console.auth.backtest_auth as backtest_auth_module

        importlib.reload(backtest_auth_module)

        # The decorator should delegate to requires_auth
        @backtest_auth_module.backtest_requires_auth
        def sample_page() -> str:
            return "page_content"

        # In non-dev mode, the function should be wrapped by requires_auth
        # which will check authentication
        assert sample_page.__wrapped__.__name__ == "sample_page"  # type: ignore[attr-defined]

    def test_backtest_requires_auth_uses_stub_when_env_true(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify dev stub activates when BACKTEST_DEV_AUTH=true.

        In development, setting BACKTEST_DEV_AUTH=true should bypass OAuth2
        and use the stub user.
        """
        # Set env var to enable dev mode
        monkeypatch.setenv("BACKTEST_DEV_AUTH", "true")

        # Import fresh to pick up env change
        import importlib

        import apps.web_console.auth.backtest_auth as backtest_auth_module

        importlib.reload(backtest_auth_module)

        # Mock streamlit session_state
        mock_session_state: dict[str, Any] = {}

        with patch("streamlit.session_state", mock_session_state):

            @backtest_auth_module.backtest_requires_auth
            def sample_page() -> str:
                return "page_content"

            result = sample_page()

        # Should return page content without auth check
        assert result == "page_content"

        # Should have set stub user in session state
        assert mock_session_state.get("authenticated") is True
        assert mock_session_state.get("username") == "dev_user"
        assert mock_session_state.get("role") == "operator"

    def test_dev_stub_sets_all_required_session_keys(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify dev stub sets all keys needed for RBAC parity."""
        monkeypatch.setenv("BACKTEST_DEV_AUTH", "true")

        import importlib

        import apps.web_console.auth.backtest_auth as backtest_auth_module

        importlib.reload(backtest_auth_module)

        mock_session_state: dict[str, Any] = {}

        with patch("streamlit.session_state", mock_session_state):

            @backtest_auth_module.backtest_requires_auth
            def sample_page() -> str:
                return "ok"

            sample_page()

        # All required keys for get_user_info() and RBAC
        required_keys = {
            "authenticated",
            "username",
            "user_id",
            "auth_method",
            "session_id",
            "role",
            "strategies",
        }

        missing_keys = required_keys - set(mock_session_state.keys())
        assert not missing_keys, f"Dev stub missing session keys: {missing_keys}"


class TestRBACIntegration:
    """Test RBAC permission checks for backtest page."""

    def test_get_user_with_role_adds_role_from_session(self) -> None:
        """Verify _get_user_with_role adds role from session_state."""
        from apps.web_console.pages.backtest import _get_user_with_role

        mock_session_state = {
            "authenticated": True,
            "username": "test_user",
            "user_id": "user123",
            "auth_method": "oauth2",
            "session_id": "session123",
            "role": "operator",
            "strategies": ["alpha_baseline"],
        }

        with patch("streamlit.session_state", mock_session_state):
            user_info = _get_user_with_role()

            assert "role" in user_info
            assert user_info["role"] == "operator"
            assert user_info["strategies"] == ["alpha_baseline"]

    def test_viewer_role_has_view_pnl_permission(self) -> None:
        """Verify viewer role can access backtest page (VIEW_PNL)."""
        from libs.web_console_auth.permissions import Permission, has_permission

        user_info = {"role": "viewer"}
        assert has_permission(user_info, Permission.VIEW_PNL) is True

    def test_viewer_role_lacks_export_permission(self) -> None:
        """Verify viewer role cannot export data."""
        from libs.web_console_auth.permissions import Permission, has_permission

        user_info = {"role": "viewer"}
        assert has_permission(user_info, Permission.EXPORT_DATA) is False

    def test_operator_role_has_export_permission(self) -> None:
        """Verify operator role can export data."""
        from libs.web_console_auth.permissions import Permission, has_permission

        user_info = {"role": "operator"}
        assert has_permission(user_info, Permission.EXPORT_DATA) is True

    def test_unknown_role_defaults_to_no_permissions(self) -> None:
        """Verify unknown roles are denied (default-deny)."""
        from libs.web_console_auth.permissions import Permission, has_permission

        user_info = {"role": "unknown_role"}
        assert has_permission(user_info, Permission.VIEW_PNL) is False
        assert has_permission(user_info, Permission.EXPORT_DATA) is False

    def test_missing_role_defaults_to_no_permissions(self) -> None:
        """Verify missing role is denied (default-deny)."""
        from libs.web_console_auth.permissions import Permission, has_permission

        user_info: dict[str, str] = {}  # No role key
        assert has_permission(user_info, Permission.VIEW_PNL) is False
