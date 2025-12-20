"""Tests for the Admin Dashboard page."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import streamlit as st

from apps.web_console.pages.admin import render_admin_page
from libs.web_console_auth.gateway_auth import AuthenticatedUser
from libs.web_console_auth.permissions import Role


class _DummyTab:
    """Minimal context manager to simulate Streamlit tabs."""

    def __init__(self, name: str, entered_log: list[str]) -> None:
        self.name = name
        self.entered_log = entered_log

    def __enter__(self) -> _DummyTab:
        self.entered_log.append(self.name)
        return self

    def __exit__(self, *_args: Any) -> bool:
        return False


def _admin_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id="admin1",
        role=Role.ADMIN,
        strategies=[],
        session_version=1,
        request_id="req-1",
    )


def _viewer_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id="viewer1",
        role=Role.VIEWER,
        strategies=[],
        session_version=1,
        request_id="req-2",
    )


def test_admin_user_sees_all_tabs() -> None:
    """Admin should render all tabs and sub-components."""

    entered: list[str] = []
    tabs = [
        _DummyTab("API Keys", entered),
        _DummyTab("System Config", entered),
        _DummyTab("Audit Logs", entered),
    ]

    with (
        patch.object(st, "title") as mock_title,
        patch.object(st, "tabs", return_value=tabs) as mock_tabs,
        patch("apps.web_console.pages.admin.render_api_key_manager") as mock_api_mgr,
        patch("apps.web_console.pages.admin.render_config_editor") as mock_config_editor,
        patch("apps.web_console.pages.admin.render_audit_log_viewer") as mock_audit_viewer,
    ):
        render_admin_page(
            user=_admin_user(),
            db_pool=MagicMock(),
            redis_client=MagicMock(),
            audit_logger=MagicMock(),
        )

    mock_title.assert_called_once_with("Admin Dashboard")
    mock_tabs.assert_called_once_with(["API Keys", "System Config", "Audit Logs"])
    mock_api_mgr.assert_called_once()
    mock_config_editor.assert_called_once()
    mock_audit_viewer.assert_called_once()
    assert entered == ["API Keys", "System Config", "Audit Logs"]


def test_user_with_no_admin_permissions_denied() -> None:
    """Users without any admin permission should be blocked from the page."""

    with (
        patch.object(st, "error") as mock_error,
        patch.object(st, "stop") as mock_stop,
        patch("apps.web_console.pages.admin.render_api_key_manager") as mock_api_mgr,
    ):
        render_admin_page(
            user=_viewer_user(),
            db_pool=MagicMock(),
            redis_client=MagicMock(),
            audit_logger=MagicMock(),
        )

    mock_error.assert_called_once()
    error_msg = mock_error.call_args[0][0]
    assert "Access denied" in error_msg
    # Error message is built dynamically from _ADMIN_PERMISSIONS
    assert "manage_api_keys" in error_msg or "manage_system_config" in error_msg
    mock_stop.assert_called_once()
    mock_api_mgr.assert_not_called()


def test_user_with_view_audit_only_can_access_page() -> None:
    """User with only VIEW_AUDIT can access the admin page (tabs handle granular checks)."""
    from libs.web_console_auth.permissions import Permission

    entered: list[str] = []
    tabs = [
        _DummyTab("API Keys", entered),
        _DummyTab("System Config", entered),
        _DummyTab("Audit Logs", entered),
    ]

    def mock_has_permission(user: Any, perm: Permission) -> bool:
        # Simulate a user that only has VIEW_AUDIT
        return perm == Permission.VIEW_AUDIT

    with (
        patch.object(st, "title"),
        patch.object(st, "tabs", return_value=tabs),
        patch("apps.web_console.pages.admin.has_permission", side_effect=mock_has_permission),
        patch("apps.web_console.pages.admin.render_api_key_manager") as mock_api_mgr,
        patch("apps.web_console.pages.admin.render_config_editor") as mock_config_editor,
        patch("apps.web_console.pages.admin.render_audit_log_viewer") as mock_audit_viewer,
    ):
        render_admin_page(
            user=_viewer_user(),  # Role doesn't matter, we're mocking has_permission
            db_pool=MagicMock(),
            redis_client=MagicMock(),
            audit_logger=MagicMock(),
        )

    # Page was accessed (not stopped), all components were called (they handle their own RBAC)
    mock_api_mgr.assert_called_once()
    mock_config_editor.assert_called_once()
    mock_audit_viewer.assert_called_once()
    assert entered == ["API Keys", "System Config", "Audit Logs"]


def test_user_with_manage_system_config_only_can_access_page() -> None:
    """User with only MANAGE_SYSTEM_CONFIG can access the admin page."""
    from libs.web_console_auth.permissions import Permission

    entered: list[str] = []
    tabs = [
        _DummyTab("API Keys", entered),
        _DummyTab("System Config", entered),
        _DummyTab("Audit Logs", entered),
    ]

    def mock_has_permission(user: Any, perm: Permission) -> bool:
        # Simulate a user that only has MANAGE_SYSTEM_CONFIG
        return perm == Permission.MANAGE_SYSTEM_CONFIG

    with (
        patch.object(st, "title"),
        patch.object(st, "tabs", return_value=tabs),
        patch("apps.web_console.pages.admin.has_permission", side_effect=mock_has_permission),
        patch("apps.web_console.pages.admin.render_api_key_manager"),
        patch("apps.web_console.pages.admin.render_config_editor"),
        patch("apps.web_console.pages.admin.render_audit_log_viewer"),
    ):
        render_admin_page(
            user=_viewer_user(),
            db_pool=MagicMock(),
            redis_client=MagicMock(),
            audit_logger=MagicMock(),
        )

    # Page rendered successfully, all three tabs were entered
    assert entered == ["API Keys", "System Config", "Audit Logs"]


def test_tab_switching_contexts_entered_in_order() -> None:
    """Tabs should be entered in order, simulating user switching."""

    entered: list[str] = []
    tabs = [
        _DummyTab("API Keys", entered),
        _DummyTab("System Config", entered),
        _DummyTab("Audit Logs", entered),
    ]

    with (
        patch.object(st, "tabs", return_value=tabs),
        patch("apps.web_console.pages.admin.render_api_key_manager"),
        patch("apps.web_console.pages.admin.render_config_editor"),
        patch("apps.web_console.pages.admin.render_audit_log_viewer"),
    ):
        render_admin_page(
            user=_admin_user(),
            db_pool=MagicMock(),
            redis_client=MagicMock(),
            audit_logger=MagicMock(),
        )

    assert entered == ["API Keys", "System Config", "Audit Logs"]


def test_dependencies_passed_to_components() -> None:
    """Ensure child components receive the provided dependencies."""

    user = _admin_user()
    db_pool = MagicMock()
    redis_client = MagicMock()
    audit_logger = MagicMock()
    tabs = [
        _DummyTab("API Keys", []),
        _DummyTab("System Config", []),
        _DummyTab("Audit Logs", []),
    ]

    with (
        patch.object(st, "tabs", return_value=tabs),
        patch("apps.web_console.pages.admin.render_api_key_manager") as mock_api_mgr,
        patch("apps.web_console.pages.admin.render_config_editor") as mock_config_editor,
        patch("apps.web_console.pages.admin.render_audit_log_viewer") as mock_audit_viewer,
    ):
        render_admin_page(
            user=user,
            db_pool=db_pool,
            redis_client=redis_client,
            audit_logger=audit_logger,
        )

    api_kwargs = mock_api_mgr.call_args.kwargs
    assert api_kwargs["user"] is user
    assert api_kwargs["db_pool"] is db_pool
    assert api_kwargs["redis_client"] is redis_client
    assert api_kwargs["audit_logger"] is audit_logger

    config_kwargs = mock_config_editor.call_args.kwargs
    assert config_kwargs["user"] is user
    assert config_kwargs["db_pool"] is db_pool
    assert config_kwargs["redis_client"] is redis_client
    assert config_kwargs["audit_logger"] is audit_logger

    audit_kwargs = mock_audit_viewer.call_args.kwargs
    assert audit_kwargs["user"] is user
    assert audit_kwargs["db_pool"] is db_pool
