"""Admin Dashboard page for platform administration."""

from __future__ import annotations

from typing import Any

import streamlit as st

from apps.web_console.auth.audit_log import AuditLogger
from apps.web_console.components.api_key_manager import render_api_key_manager
from apps.web_console.components.audit_log_viewer import render_audit_log_viewer
from apps.web_console.components.config_editor import render_config_editor
from libs.web_console_auth.gateway_auth import AuthenticatedUser
from libs.web_console_auth.permissions import Permission, has_permission

__all__ = ["render_admin_page"]


_ADMIN_PERMISSIONS = {
    Permission.MANAGE_API_KEYS,
    Permission.MANAGE_SYSTEM_CONFIG,
    Permission.VIEW_AUDIT,
}


def render_admin_page(
    user: AuthenticatedUser,
    db_pool: Any,
    redis_client: Any,
    audit_logger: AuditLogger,
) -> None:
    """Render the Admin Dashboard page with tabbed sections.

    Access requires at least one of: MANAGE_API_KEYS, MANAGE_SYSTEM_CONFIG, or VIEW_AUDIT.
    Each tab enforces its own specific permission check internally.
    """

    st.title("Admin Dashboard")

    # Allow access if user has ANY admin permission; tabs enforce granular checks
    if not any(has_permission(user, p) for p in _ADMIN_PERMISSIONS):
        perm_names = ", ".join(p.value for p in _ADMIN_PERMISSIONS)
        st.error(f"Access denied: No admin permissions. Requires one of: {perm_names}")
        st.stop()
        return

    api_tab, config_tab, audit_tab = st.tabs(["API Keys", "System Config", "Audit Logs"])

    with api_tab:
        render_api_key_manager(
            user=user,
            db_pool=db_pool,
            audit_logger=audit_logger,
            redis_client=redis_client,
        )

    with config_tab:
        render_config_editor(
            user=user,
            db_pool=db_pool,
            audit_logger=audit_logger,
            redis_client=redis_client,
        )

    with audit_tab:
        render_audit_log_viewer(user=user, db_pool=db_pool)
