"""User Management / RBAC Admin page (P6T16.2).

Admin page for user role management, strategy grants, activity monitoring,
and force logout. Requires MANAGE_USERS permission (ADMIN only).
"""

from __future__ import annotations

import logging
from typing import Any

from nicegui import ui
from psycopg.rows import dict_row
from redis.exceptions import RedisError

from apps.web_console_ng.auth.db_role import verify_db_role
from apps.web_console_ng.auth.middleware import get_current_user, requires_auth
from apps.web_console_ng.auth.session_store import (
    invalidate_redis_sessions_for_user,
)
from apps.web_console_ng.components.role_selector import render_role_change_dialog
from apps.web_console_ng.components.strategy_grants import render_strategy_grants_dialog
from apps.web_console_ng.components.user_activity import render_user_activity_log
from apps.web_console_ng.components.user_table import render_user_table
from apps.web_console_ng.core.database import get_db_pool
from apps.web_console_ng.ui.layout import main_layout
from libs.platform.web_console_auth.audit_log import AuditLogger
from libs.platform.web_console_auth.permissions import Permission, has_permission
from libs.web_console_services.user_management import (
    change_user_role,
    ensure_user_provisioned,
    get_user_strategies,
    grant_strategy,
    list_strategies,
    list_users,
    revoke_strategy,
)

logger = logging.getLogger(__name__)


@ui.page("/admin/users")
@requires_auth
@main_layout
async def admin_users_page() -> None:
    """User management admin page."""
    user = get_current_user()
    if not has_permission(user, Permission.MANAGE_USERS):
        ui.label("Admin access required: MANAGE_USERS permission needed").classes("text-red-500")
        return

    db_pool = get_db_pool()
    if db_pool is None:
        ui.label("Database unavailable").classes("text-red-500")
        return

    audit = AuditLogger(db_pool)
    admin_user_id = user.get("user_id", "unknown")

    # Load users
    users = await list_users(db_pool)

    # Page title
    ui.label("User Management").classes("text-2xl font-bold mb-4")

    # Manual user provisioning section
    with ui.row().classes("w-full items-end gap-2 mb-4"):
        provision_input = ui.input("Provision User ID").props("outlined dense").classes("w-64")

        async def _provision_user() -> None:
            current = get_current_user()
            current_uid = current.get("user_id", "unknown")
            if not has_permission(current, Permission.MANAGE_USERS) or not await verify_db_role(
                db_pool, current_uid, Permission.MANAGE_USERS
            ):
                try:
                    await audit.log_action(
                        user_id=current_uid,
                        action="provision_user_denied",
                        resource_type="user",
                        resource_id="",
                        outcome="denied",
                        details={"reason": "permission_denied"},
                    )
                except Exception:
                    logger.debug("audit_log_provision_denied_failed")
                ui.notify("Permission denied", type="negative")
                return
            target_id = provision_input.value
            if not target_id or not target_id.strip():
                ui.notify("Enter a user ID", type="warning")
                return
            target_id = target_id.strip()
            current_uid = current.get("user_id", "unknown")

            # Self-provisioning guard: prevent admin from provisioning themselves
            # as viewer, which would cause middleware role override to demote them.
            if target_id == current_uid:
                ui.notify("Cannot provision yourself — use role change instead", type="warning")
                return

            created, msg = await ensure_user_provisioned(
                db_pool, target_id, "viewer", current_uid, audit
            )
            if created:
                # Invalidate __none__ sentinel so middleware picks up new role
                try:
                    from apps.web_console_ng.core.redis_ha import get_redis_store

                    store = get_redis_store()
                    redis_client = await store.get_master()
                    await redis_client.delete(f"ng_role_cache:{target_id}")
                except Exception:
                    logger.debug("role_cache_invalidation_after_provision_failed", extra={"user_id": target_id})
                ui.notify(msg, type="positive")
                nonlocal users
                users = await list_users(db_pool)
                user_grid.refresh()
            elif msg.startswith("Database error"):
                ui.notify(msg, type="negative")
            else:
                ui.notify(msg, type="info")

        ui.button("Provision", on_click=_provision_user, icon="person_add").props("dense")

    # Role change callback
    async def _on_role_change(target_user_id: str) -> None:
        current = get_current_user()
        current_uid = current.get("user_id", "unknown")
        if not has_permission(current, Permission.MANAGE_USERS) or not await verify_db_role(
            db_pool, current_uid, Permission.MANAGE_USERS
        ):
            ui.notify("Permission denied", type="negative")
            return
        current_uid = current.get("user_id", "unknown")

        # Self-edit guard (page-level + service-level)
        if target_user_id == current_uid:
            await audit.log_action(
                user_id=current_uid,
                action="role_change_denied",
                resource_type="user",
                resource_id=target_user_id,
                outcome="denied",
                details={"reason": "self_edit"},
            )
            ui.notify("Cannot change your own role", type="warning")
            return

        target = next((u for u in users if u.user_id == target_user_id), None)
        if target is None:
            ui.notify("User not found — list may be stale", type="warning")
            return

        async def _do_role_change(uid: str, new_role: str, reason: str) -> None:
            current = get_current_user()
            current_uid = current.get("user_id", "unknown")
            if not has_permission(current, Permission.MANAGE_USERS) or not await verify_db_role(
                db_pool, current_uid, Permission.MANAGE_USERS
            ):
                ui.notify("Permission denied", type="negative")
                return
            nonlocal users
            # Last-admin guard (page-level check)
            admin_count = sum(1 for u in users if u.role == "admin")
            t = next((u for u in users if u.user_id == uid), None)
            if t and t.role == "admin" and admin_count <= 1 and new_role != "admin":
                await audit.log_action(
                    user_id=current_uid,
                    action="role_change_denied",
                    resource_type="user",
                    resource_id=uid,
                    outcome="denied",
                    details={"reason": "last_admin", "attempted_role": new_role},
                )
                ui.notify("Cannot remove the last admin", type="negative")
                return

            success, msg = await change_user_role(
                db_pool, uid, new_role, current_uid, audit, reason
            )
            if success:
                # Best-effort: invalidate Redis role cache + sessions
                try:
                    from apps.web_console_ng.core.redis_ha import get_redis_store

                    store = get_redis_store()
                    redis_client = await store.get_master()
                    await redis_client.delete(f"ng_role_cache:{uid}")
                except Exception:
                    logger.warning("role_cache_invalidation_failed", extra={"user_id": uid})
                try:
                    count = await invalidate_redis_sessions_for_user(uid)
                    logger.info("sessions_invalidated_after_role_change", extra={"user_id": uid, "count": count})
                except Exception:
                    logger.warning("session_invalidation_after_role_change_failed", extra={"user_id": uid})
                ui.notify(f"Role updated to {new_role}", type="positive")
                users = await list_users(db_pool)
                user_grid.refresh()
            else:
                ui.notify(msg, type="negative")

        render_role_change_dialog(target_user_id, target.role, on_confirm=_do_role_change)

    # Strategy grants callback
    async def _on_view_strategies(target_user_id: str) -> None:
        current = get_current_user()
        current_uid = current.get("user_id", "unknown")
        if not has_permission(current, Permission.MANAGE_USERS) or not await verify_db_role(
            db_pool, current_uid, Permission.MANAGE_USERS
        ):
            try:
                await audit.log_action(
                    user_id=current_uid,
                    action="view_strategies_denied",
                    resource_type="user",
                    resource_id=target_user_id,
                    outcome="denied",
                    details={"reason": "permission_denied"},
                )
            except Exception:
                logger.debug("audit_log_view_strategies_denied_failed")
            ui.notify("Permission denied", type="negative")
            return
        try:
            assigned = await get_user_strategies(db_pool, target_user_id)
            available = await list_strategies(db_pool)
        except Exception:
            logger.warning("strategy_dialog_load_failed", extra={"user_id": target_user_id})
            ui.notify("Failed to load strategy data", type="negative")
            return

        async def _grant(uid: str, sid: str) -> None:
            current = get_current_user()
            cur_uid = current.get("user_id", "unknown")
            if not has_permission(current, Permission.MANAGE_USERS) or not await verify_db_role(
                db_pool, cur_uid, Permission.MANAGE_USERS
            ):
                ui.notify("Permission denied", type="negative")
                return
            success, msg = await grant_strategy(
                db_pool, uid, sid, cur_uid, audit
            )
            if success:
                ui.notify(msg, type="positive")
            else:
                ui.notify(msg, type="negative")

        async def _revoke(uid: str, sid: str) -> None:
            current = get_current_user()
            cur_uid = current.get("user_id", "unknown")
            if not has_permission(current, Permission.MANAGE_USERS) or not await verify_db_role(
                db_pool, cur_uid, Permission.MANAGE_USERS
            ):
                ui.notify("Permission denied", type="negative")
                return
            success, msg = await revoke_strategy(
                db_pool, uid, sid, cur_uid, audit
            )
            if success:
                ui.notify(msg, type="positive")
            else:
                ui.notify(msg, type="negative")

        render_strategy_grants_dialog(
            target_user_id, assigned, available, on_grant=_grant, on_revoke=_revoke
        )

    # Activity log callback
    async def _on_view_activity(target_user_id: str) -> None:
        current = get_current_user()
        current_uid = current.get("user_id", "unknown")
        if not has_permission(current, Permission.MANAGE_USERS) or not await verify_db_role(
            db_pool, current_uid, Permission.MANAGE_USERS
        ):
            try:
                await audit.log_action(
                    user_id=current_uid,
                    action="view_activity_denied",
                    resource_type="user",
                    resource_id=target_user_id,
                    outcome="denied",
                    details={"reason": "permission_denied"},
                )
            except Exception:
                logger.debug("audit_log_view_activity_denied_failed")
            ui.notify("Permission denied", type="negative")
            return
        events = await _fetch_user_activity(db_pool, target_user_id)
        if events is None:
            ui.notify("Failed to load activity log", type="negative")
            return
        render_user_activity_log(target_user_id, events)

    # Force logout callback
    # NOTE: Force-logout invalidates Redis sessions but does NOT terminate
    # active NiceGUI WebSocket connections.  The role-override middleware
    # enforces the DB role on every subsequent HTTP request, preventing
    # privilege escalation via stale WebSocket sessions.
    async def _on_force_logout(target_user_id: str) -> None:
        current = get_current_user()
        current_uid = current.get("user_id", "unknown")
        if not has_permission(current, Permission.MANAGE_USERS) or not await verify_db_role(
            db_pool, current_uid, Permission.MANAGE_USERS
        ):
            ui.notify("Permission denied", type="negative")
            return

        if target_user_id == current_uid:
            try:
                await audit.log_action(
                    user_id=current_uid,
                    action="force_logout_denied",
                    resource_type="user",
                    resource_id=target_user_id,
                    outcome="denied",
                    details={"reason": "self_logout"},
                )
            except Exception:
                logger.debug("audit_log_self_logout_denied_failed")
            ui.notify("Cannot force-logout yourself — use normal logout", type="warning")
            return

        with ui.dialog() as dialog, ui.card():
            ui.label(f"Force logout {target_user_id}?").classes("text-lg font-bold")
            ui.label("They will be signed out of all sessions.").classes("text-gray-500")

            async def _confirm_logout() -> None:
                cur = get_current_user()
                cur_uid = cur.get("user_id", "unknown")
                if not has_permission(cur, Permission.MANAGE_USERS) or not await verify_db_role(
                    db_pool, cur_uid, Permission.MANAGE_USERS
                ):
                    try:
                        await audit.log_action(
                            user_id=cur_uid,
                            action="force_logout_denied",
                            resource_type="user",
                            resource_id=target_user_id,
                            outcome="denied",
                            details={"reason": "permission_revoked"},
                        )
                    except Exception:
                        logger.debug("audit_log_force_logout_denied_failed")
                    ui.notify("Permission denied", type="negative")
                    dialog.close()
                    return
                actor_id = cur.get("user_id", "unknown")
                try:
                    count = await invalidate_redis_sessions_for_user(target_user_id)
                except (RedisError, Exception) as exc:
                    logger.warning(
                        "force_logout_failed",
                        extra={"target": target_user_id, "error": str(exc)},
                    )
                    try:
                        await audit.log_action(
                            user_id=actor_id,
                            action="force_logout_failed",
                            resource_type="user",
                            resource_id=target_user_id,
                            outcome="failed",
                            details={"error": str(exc)},
                        )
                    except Exception:
                        logger.debug("audit_log_force_logout_failed_error")
                    dialog.close()
                    ui.notify("Force logout failed — see logs", type="negative")
                    return
                # Invalidation succeeded — audit is best-effort
                try:
                    await audit.log_admin_change(
                        admin_user_id=actor_id,
                        action="force_logout",
                        target_user_id=target_user_id,
                        details={"sessions_removed": count},
                    )
                except Exception:
                    logger.debug("audit_log_force_logout_success_error")
                dialog.close()
                ui.notify(f"Logged out {target_user_id} ({count} sessions)", type="positive")

            with ui.row().classes("justify-end gap-2 mt-4"):
                ui.button("Cancel", on_click=dialog.close).props("flat")
                ui.button("Force Logout", on_click=_confirm_logout, color="orange")

        dialog.open()

    # User table (refreshable)
    @ui.refreshable
    def user_grid() -> None:
        render_user_table(
            users,
            on_role_change=_on_role_change,
            on_view_strategies=_on_view_strategies,
            on_view_activity=_on_view_activity,
            on_force_logout=_on_force_logout,
            current_user_id=admin_user_id,
        )

    user_grid()


async def _fetch_user_activity(
    db_pool: Any, target_user_id: str, limit: int = 100
) -> list[dict[str, Any]] | None:
    """Fetch audit log events related to a user.

    Captures: (a) actions BY the user, (b) actions targeting the user exactly,
    (c) denied/failed events with composite resource_id like "{user_id}:{strategy_id}".

    Returns None on query failure (distinct from empty list = no events).
    """
    # Escape SQL wildcards in user_id for LIKE clause
    escaped = target_user_id.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    like_pattern = f"{escaped}:%"

    try:
        async with db_pool.connection() as conn:
            async with conn.cursor(row_factory=dict_row) as cur:
                # Use UNION for index-friendly queries instead of OR (PR #148 review)
                await cur.execute(
                    """
                    (SELECT user_id, action, resource_type, resource_id,
                            outcome, details, timestamp
                     FROM audit_log WHERE user_id = %s)
                    UNION
                    (SELECT user_id, action, resource_type, resource_id,
                            outcome, details, timestamp
                     FROM audit_log WHERE resource_id = %s)
                    UNION
                    (SELECT user_id, action, resource_type, resource_id,
                            outcome, details, timestamp
                     FROM audit_log WHERE resource_id LIKE %s ESCAPE '\\')
                    ORDER BY timestamp DESC
                    LIMIT %s
                    """,
                    (target_user_id, target_user_id, like_pattern, limit),
                )
                return list(await cur.fetchall())
    except Exception:
        logger.warning("user_activity_query_failed", extra={"user_id": target_user_id})
        return None
