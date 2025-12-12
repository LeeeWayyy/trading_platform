"""Admin User Management Page.

[v1.2] Page-level permission denial logged via AuditLogger.

Requires MANAGE_USERS permission. Provides:
- User list with roles and strategy counts
- Role change with confirmation
- Strategy assignment per user
- Bulk operations (role change, strategy grant/revoke)
- Search and filter functionality
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import streamlit as st

from apps.web_console.auth.audit_log import AuditLogger
from apps.web_console.auth.permissions import Permission, has_permission
from apps.web_console.components.bulk_operations import (
    render_bulk_role_change,
    render_bulk_strategy_operations,
)
from apps.web_console.components.strategy_assignment import render_strategy_assignment
from apps.web_console.components.user_role_editor import render_role_editor
from apps.web_console.services.user_management import UserInfo, list_users

logger = logging.getLogger(__name__)


def render_admin_users(
    user: dict[str, Any],
    db_pool: Any,
    audit_logger: AuditLogger,
) -> None:
    """Render admin user management page.

    Args:
        user: Current authenticated user dict with 'sub', 'role', etc.
        db_pool: Database connection pool
        audit_logger: Audit logger instance
    """

    # Permission check (defense in depth - should also be checked at routing)
    if not has_permission(user, Permission.MANAGE_USERS):
        st.error("Permission denied: MANAGE_USERS required")
        # [v1.2] Log to AuditLogger, not just logger
        _log_page_denial_sync(audit_logger, user.get("sub"), user.get("role"))
        logger.warning(
            "admin_page_access_denied",
            extra={"user_id": user.get("sub"), "role": user.get("role")},
        )
        st.stop()

    st.title("User Management")
    st.caption("Manage user roles and strategy access")

    admin_user_id = user.get("sub", "unknown")

    # Fetch users
    users = _list_users_sync(db_pool)

    if not users:
        st.info("No users provisioned yet. Use `scripts/manage_roles.py` to bootstrap admin.")
        return

    # Search/filter
    col1, col2 = st.columns([3, 1])
    with col1:
        search_query = st.text_input(
            "Search users",
            placeholder="Filter by user ID...",
            key="user_search",
        )
    with col2:
        role_filter = st.selectbox(
            "Filter by role",
            ["All", "admin", "operator", "viewer"],
            key="role_filter",
        )

    # Apply filters
    filtered_users = users
    if search_query:
        filtered_users = [u for u in filtered_users if search_query.lower() in u.user_id.lower()]
    if role_filter != "All":
        filtered_users = [u for u in filtered_users if u.role == role_filter]

    # [v1.2] Tabs for single-user vs bulk operations
    main_tab1, main_tab2 = st.tabs(["Individual Users", "Bulk Operations"])

    with main_tab1:
        # User list
        st.subheader(f"Users ({len(filtered_users)} of {len(users)})")

        for user_info in filtered_users:
            with st.expander(
                f"**{user_info.user_id}** - {user_info.role} ({user_info.strategy_count} strategies)",
                expanded=False,
            ):
                # User details
                st.markdown(f"**Session Version:** {user_info.session_version}")
                st.markdown(f"**Last Updated:** {user_info.updated_at}")
                if user_info.updated_by:
                    st.markdown(f"**Updated By:** {user_info.updated_by}")

                st.divider()

                # Tabs for role and strategy management
                tab1, tab2 = st.tabs(["Role", "Strategies"])

                with tab1:
                    render_role_editor(
                        user_id=user_info.user_id,
                        current_role=user_info.role,
                        admin_user_id=admin_user_id,
                        db_pool=db_pool,
                        audit_logger=audit_logger,
                    )

                with tab2:
                    render_strategy_assignment(
                        user_id=user_info.user_id,
                        admin_user_id=admin_user_id,
                        db_pool=db_pool,
                        audit_logger=audit_logger,
                    )

    with main_tab2:
        # [v1.2] Bulk operations with double-confirmation
        st.subheader("Bulk Operations")
        st.warning("⚠️ Bulk operations require double confirmation to prevent accidental changes.")

        bulk_tab1, bulk_tab2 = st.tabs(["Bulk Role Change", "Bulk Strategy Operations"])

        with bulk_tab1:
            render_bulk_role_change(users, admin_user_id, db_pool, audit_logger)

        with bulk_tab2:
            render_bulk_strategy_operations(users, admin_user_id, db_pool, audit_logger)


def _log_page_denial_sync(audit_logger: AuditLogger, user_id: str | None, role: str | None) -> None:
    """[v1.2] Log page-level permission denial to AuditLogger."""

    import concurrent.futures

    async def _log() -> None:
        await audit_logger.log_action(
            user_id=user_id,
            action="admin_page_access_denied",
            resource_type="page",
            resource_id="admin_users",
            outcome="denied",
            details={"role": role, "required_permission": "MANAGE_USERS"},
        )

    try:
        _ = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            pool.submit(asyncio.run, _log()).result(timeout=5.0)
    except RuntimeError:
        asyncio.run(_log())


def _list_users_sync(db_pool: Any) -> list[UserInfo]:
    """Sync wrapper for list_users."""

    import concurrent.futures

    try:
        _ = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            result: list[UserInfo] = pool.submit(asyncio.run, list_users(db_pool)).result(timeout=10.0)
            return result
    except RuntimeError:
        return asyncio.run(list_users(db_pool))


__all__ = ["render_admin_users"]
