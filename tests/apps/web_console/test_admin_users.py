"""Integration tests for admin users page."""

from unittest.mock import AsyncMock, MagicMock, patch

import streamlit as st

from apps.web_console.pages.admin_users import render_admin_users
from apps.web_console.auth.permissions import Permission


class TestAdminUsersPage:
    """Tests for admin user management page."""

    def test_access_denied_without_permission(self):
        """Test non-admin users cannot access page."""

        user = {"sub": "viewer1", "role": "viewer"}
        mock_pool = MagicMock()
        mock_audit = AsyncMock()

        with patch.object(st, "error") as mock_error:
            with patch.object(st, "stop") as mock_stop:
                render_admin_users(user, mock_pool, mock_audit)

                mock_error.assert_called()
                mock_stop.assert_called()

    def test_admin_can_access(self):
        """Test admin users can access page."""

        user = {"sub": "admin1", "role": "admin"}
        mock_pool = MagicMock()
        mock_audit = MagicMock()

        with patch("apps.web_console.pages.admin_users._list_users_sync", return_value=[]):
            with patch.object(st, "title") as mock_title:
                render_admin_users(user, mock_pool, mock_audit)

                mock_title.assert_called_with("User Management")

    def test_page_denial_logged_to_audit(self):
        """[v1.2] Test page access denial is logged to AuditLogger."""

        user = {"sub": "viewer1", "role": "viewer"}
        mock_pool = MagicMock()
        mock_audit = AsyncMock()

        with patch("apps.web_console.pages.admin_users._log_page_denial_sync") as mock_log:
            with patch.object(st, "error"):
                with patch.object(st, "stop"):
                    render_admin_users(user, mock_pool, mock_audit)

                    mock_log.assert_called_once()
