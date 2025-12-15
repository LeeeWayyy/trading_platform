"""Bulk operations component with double-confirmation.

[v1.2] Complete bulk operations: role changes, strategy grants, strategy revokes.
All with double-confirmation (preview + type CONFIRM).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Coroutine
from typing import Any, TypeVar

import streamlit as st

from apps.web_console.auth.audit_log import AuditLogger
from apps.web_console.components.csrf_protection import (
    generate_csrf_token,
    rotate_csrf_token,
    verify_csrf_token,
)
from apps.web_console.services.user_management import (
    StrategyInfo,
    UserInfo,
    bulk_change_roles,
    bulk_grant_strategy,
    bulk_revoke_strategy,
    list_strategies,
)

logger = logging.getLogger(__name__)


def render_bulk_role_change(
    users: list[UserInfo],
    admin_user_id: str,
    db_pool: Any,
    audit_logger: Any,
) -> None:
    """Render bulk role change UI with double-confirmation."""

    st.subheader("Bulk Role Change")
    st.caption("Change roles for multiple users at once")

    # First confirmation state
    confirm1_key = "bulk_role_confirm1"
    confirm2_key = "bulk_role_confirm2"

    csrf_token = generate_csrf_token()

    with st.form("bulk_role_form"):
        # User selection
        user_options = {u.user_id: f"{u.user_id} ({u.role})" for u in users}
        selected_users = st.multiselect(
            "Select Users",
            options=list(user_options.keys()),
            format_func=lambda x: user_options.get(x, x),
            key="bulk_users",
        )

        new_role = st.selectbox("New Role", ["viewer", "operator", "admin"])

        reason = st.text_area(
            "Reason for bulk change (required)",
            placeholder="Enter justification for bulk role change...",
            key="bulk_reason",
        )

        submitted_csrf = st.text_input(
            "csrf",
            value=csrf_token,
            type="password",
            label_visibility="hidden",
            key="bulk_csrf",
        )

        submitted = st.form_submit_button("Preview Changes", type="primary")

        if submitted:
            if not verify_csrf_token(submitted_csrf):
                st.error("Invalid form submission.")
                _log_csrf_failure_sync(audit_logger, admin_user_id, "bulk_role_change")
                rotate_csrf_token()
                return

            if not selected_users:
                st.error("Select at least one user.")
                return

            if not reason or len(reason.strip()) < 10:
                st.error("Reason must be at least 10 characters.")
                return

            # First confirmation - store CSRF token for later verification
            st.session_state[confirm1_key] = {
                "users": selected_users,
                "new_role": new_role,
                "reason": reason.strip(),
                "csrf_token": submitted_csrf,  # [v1.3] Store for execution-time verification
            }
            st.rerun()

    # First confirmation dialog
    if st.session_state.get(confirm1_key) and not st.session_state.get(confirm2_key):
        pending = st.session_state[confirm1_key]
        st.warning(
            f"**First Confirmation - Bulk Role Change**\n\n"
            f"Users: {len(pending['users'])} selected\n\n"
            f"New Role: {pending['new_role']}\n\n"
            f"This will invalidate all active sessions for these users."
        )

        col1, col2 = st.columns(2)
        with col1:
            if st.button("Proceed to Final Confirmation", type="primary"):
                st.session_state[confirm2_key] = True
                st.rerun()
        with col2:
            if st.button("Cancel"):
                del st.session_state[confirm1_key]
                st.rerun()

    # Second (final) confirmation dialog - DOUBLE CONFIRM
    if st.session_state.get(confirm1_key) and st.session_state.get(confirm2_key):
        pending = st.session_state[confirm1_key]
        st.error(
            f"**FINAL CONFIRMATION - BULK ROLE CHANGE**\n\n"
            f"⚠️ You are about to change roles for **{len(pending['users'])} users**\n\n"
            f"This action cannot be easily undone.\n\n"
            f"Type 'CONFIRM' below to proceed."
        )

        confirm_text = st.text_input("Type CONFIRM to proceed", key="confirm_text")

        col1, col2 = st.columns(2)
        with col1:
            if st.button("Execute Bulk Change", type="primary"):
                if confirm_text != "CONFIRM":
                    st.error("You must type 'CONFIRM' to proceed.")
                    return

                # [v1.3] Re-verify CSRF at execution time
                if not verify_csrf_token(pending.get("csrf_token", "")):
                    st.error("Session expired. Please refresh and try again.")
                    _log_csrf_failure_sync(audit_logger, admin_user_id, "bulk_role_execute")
                    del st.session_state[confirm1_key]
                    del st.session_state[confirm2_key]
                    rotate_csrf_token()
                    return

                # Execute bulk change
                results = _bulk_change_roles_sync(
                    db_pool,
                    pending["users"],
                    pending["new_role"],
                    admin_user_id,
                    audit_logger,
                    pending["reason"],
                )

                success_count = sum(1 for s, _ in results.values() if s)
                fail_count = len(results) - success_count

                st.success(f"Completed: {success_count} succeeded, {fail_count} failed")

                for user_id, (success, msg) in results.items():
                    if success:
                        st.write(f"✅ {user_id}: {msg}")
                    else:
                        st.write(f"❌ {user_id}: {msg}")

                rotate_csrf_token()
                del st.session_state[confirm1_key]
                del st.session_state[confirm2_key]

        with col2:
            if st.button("Cancel", key="cancel_final"):
                del st.session_state[confirm1_key]
                del st.session_state[confirm2_key]
                st.rerun()


def render_bulk_strategy_operations(
    users: list[UserInfo],
    admin_user_id: str,
    db_pool: Any,
    audit_logger: AuditLogger,
) -> None:
    """[v1.2] Render bulk strategy operations (grant AND revoke) with double-confirmation."""

    st.subheader("Bulk Strategy Operations")

    strategies = _list_strategies_sync(db_pool)
    if not strategies:
        st.info("No strategies configured.")
        return

    # Tab for grant vs revoke
    tab_grant, tab_revoke = st.tabs(["Bulk Grant", "Bulk Revoke"])

    with tab_grant:
        _render_bulk_strategy_grant(users, admin_user_id, db_pool, audit_logger, strategies)

    with tab_revoke:
        _render_bulk_strategy_revoke(users, admin_user_id, db_pool, audit_logger, strategies)


def _render_bulk_strategy_grant(
    users: list[UserInfo],
    admin_user_id: str,
    db_pool: Any,
    audit_logger: AuditLogger,
    strategies: list[StrategyInfo],
) -> None:
    """Bulk strategy grant with double-confirmation."""

    confirm1_key = "bulk_grant_confirm1"
    confirm2_key = "bulk_grant_confirm2"

    csrf_token = generate_csrf_token()

    with st.form("bulk_grant_form"):
        user_options = {u.user_id: f"{u.user_id} ({u.role})" for u in users}
        selected_users = st.multiselect(
            "Select Users",
            options=list(user_options.keys()),
            format_func=lambda x: user_options.get(x, x),
            key="bulk_grant_users",
        )
        strategy_options = {s.strategy_id: f"{s.name}" for s in strategies}
        selected_strategy = st.selectbox(
            "Strategy to Grant",
            options=list(strategy_options.keys()),
            format_func=lambda x: strategy_options.get(x, x),
            key="bulk_grant_strat",
        )
        submitted_csrf = st.text_input(
            "csrf",
            value=csrf_token,
            type="password",
            label_visibility="hidden",
            key="bulk_grant_csrf",
        )
        submitted = st.form_submit_button("Preview Grant", type="primary")

        if submitted:
            if not verify_csrf_token(submitted_csrf):
                st.error("Invalid form submission.")
                _log_csrf_failure_sync(audit_logger, admin_user_id, "bulk_strategy_grant")
                rotate_csrf_token()
                return
            if not selected_users:
                st.error("Select at least one user.")
                return
            st.session_state[confirm1_key] = {
                "users": selected_users,
                "strategy_id": selected_strategy,
                "csrf_token": submitted_csrf,  # [v1.3] Store for execution-time verification
            }
            st.rerun()

    # First confirmation
    if st.session_state.get(confirm1_key) and not st.session_state.get(confirm2_key):
        pending = st.session_state[confirm1_key]
        st.warning(f"**Grant {pending['strategy_id']} to {len(pending['users'])} users?**")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Proceed to Final", key="grant_proceed"):
                st.session_state[confirm2_key] = True
                st.rerun()
        with col2:
            if st.button("Cancel", key="grant_cancel1"):
                del st.session_state[confirm1_key]
                st.rerun()

    # Second confirmation (type CONFIRM)
    if st.session_state.get(confirm1_key) and st.session_state.get(confirm2_key):
        pending = st.session_state[confirm1_key]
        st.error(
            f"**FINAL: Grant {pending['strategy_id']} to {len(pending['users'])} users**\n\n"
            f"Type 'CONFIRM' to proceed."
        )
        confirm_text = st.text_input("Type CONFIRM", key="grant_confirm_text")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Execute Grant", type="primary", key="grant_exec"):
                if confirm_text != "CONFIRM":
                    st.error("Type 'CONFIRM' to proceed.")
                    return
                # [v1.3] Re-verify CSRF at execution time
                if not verify_csrf_token(pending.get("csrf_token", "")):
                    st.error("Session expired. Please refresh and try again.")
                    _log_csrf_failure_sync(audit_logger, admin_user_id, "bulk_grant_execute")
                    del st.session_state[confirm1_key]
                    del st.session_state[confirm2_key]
                    rotate_csrf_token()
                    return
                results = _bulk_grant_strategy_sync(
                    db_pool,
                    pending["users"],
                    pending["strategy_id"],
                    admin_user_id,
                    audit_logger,
                )
                _show_bulk_results(results, "Grant")
                rotate_csrf_token()
                del st.session_state[confirm1_key]
                del st.session_state[confirm2_key]
        with col2:
            if st.button("Cancel", key="grant_cancel2"):
                del st.session_state[confirm1_key]
                del st.session_state[confirm2_key]
                st.rerun()


def _render_bulk_strategy_revoke(
    users: list[UserInfo],
    admin_user_id: str,
    db_pool: Any,
    audit_logger: AuditLogger,
    strategies: list[StrategyInfo],
) -> None:
    """[v1.2] Bulk strategy revoke with double-confirmation."""

    confirm1_key = "bulk_revoke_confirm1"
    confirm2_key = "bulk_revoke_confirm2"

    csrf_token = generate_csrf_token()

    with st.form("bulk_revoke_form"):
        user_options = {u.user_id: f"{u.user_id} ({u.role})" for u in users}
        selected_users = st.multiselect(
            "Select Users",
            options=list(user_options.keys()),
            format_func=lambda x: user_options.get(x, x),
            key="bulk_revoke_users",
        )
        strategy_options = {s.strategy_id: f"{s.name}" for s in strategies}
        selected_strategy = st.selectbox(
            "Strategy to Revoke",
            options=list(strategy_options.keys()),
            format_func=lambda x: strategy_options.get(x, x),
            key="bulk_revoke_strat",
        )
        submitted_csrf = st.text_input(
            "csrf",
            value=csrf_token,
            type="password",
            label_visibility="hidden",
            key="bulk_revoke_csrf",
        )
        submitted = st.form_submit_button("Preview Revoke", type="primary")

        if submitted:
            if not verify_csrf_token(submitted_csrf):
                st.error("Invalid form submission.")
                _log_csrf_failure_sync(audit_logger, admin_user_id, "bulk_strategy_revoke")
                rotate_csrf_token()
                return
            if not selected_users:
                st.error("Select at least one user.")
                return
            st.session_state[confirm1_key] = {
                "users": selected_users,
                "strategy_id": selected_strategy,
                "csrf_token": submitted_csrf,  # [v1.3] Store for execution-time verification
            }
            st.rerun()

    # First confirmation
    if st.session_state.get(confirm1_key) and not st.session_state.get(confirm2_key):
        pending = st.session_state[confirm1_key]
        st.warning(f"**Revoke {pending['strategy_id']} from {len(pending['users'])} users?**")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Proceed to Final", key="revoke_proceed"):
                st.session_state[confirm2_key] = True
                st.rerun()
        with col2:
            if st.button("Cancel", key="revoke_cancel1"):
                del st.session_state[confirm1_key]
                st.rerun()

    # Second confirmation (type CONFIRM)
    if st.session_state.get(confirm1_key) and st.session_state.get(confirm2_key):
        pending = st.session_state[confirm1_key]
        st.error(
            f"**FINAL: Revoke {pending['strategy_id']} from {len(pending['users'])} users**\n\n"
            f"Type 'CONFIRM' to proceed."
        )
        confirm_text = st.text_input("Type CONFIRM", key="revoke_confirm_text")
        col1, col2 = st.columns(2)
        with col1:
            if st.button("Execute Revoke", type="primary", key="revoke_exec"):
                if confirm_text != "CONFIRM":
                    st.error("Type 'CONFIRM' to proceed.")
                    return
                # [v1.3] Re-verify CSRF at execution time
                if not verify_csrf_token(pending.get("csrf_token", "")):
                    st.error("Session expired. Please refresh and try again.")
                    _log_csrf_failure_sync(audit_logger, admin_user_id, "bulk_revoke_execute")
                    del st.session_state[confirm1_key]
                    del st.session_state[confirm2_key]
                    rotate_csrf_token()
                    return
                results = _bulk_revoke_strategy_sync(
                    db_pool,
                    pending["users"],
                    pending["strategy_id"],
                    admin_user_id,
                    audit_logger,
                )
                _show_bulk_results(results, "Revoke")
                rotate_csrf_token()
                del st.session_state[confirm1_key]
                del st.session_state[confirm2_key]
        with col2:
            if st.button("Cancel", key="revoke_cancel2"):
                del st.session_state[confirm1_key]
                del st.session_state[confirm2_key]
                st.rerun()


def _show_bulk_results(results: dict[str, tuple[bool, str]], action: str) -> None:
    """Display per-user results for bulk operations."""

    success_count = sum(1 for s, _ in results.values() if s)
    fail_count = len(results) - success_count
    st.success(f"{action} complete: {success_count} succeeded, {fail_count} failed")
    for user_id, (success, msg) in results.items():
        if success:
            st.write(f"✅ {user_id}: {msg}")
        else:
            st.write(f"❌ {user_id}: {msg}")


T = TypeVar("T")


def _run_async(coro: Coroutine[Any, Any, T]) -> T:
    """[v1.2] Run async in fresh event loop via ThreadPoolExecutor.

    This pattern avoids conflicts with Streamlit's event loop while
    providing thread-safe async execution.
    """

    import concurrent.futures

    try:
        _ = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result(timeout=30.0)
    except RuntimeError:
        return asyncio.run(coro)


def _log_csrf_failure_sync(audit_logger: AuditLogger, admin_user_id: str, action: str) -> None:
    """Log CSRF failure to audit trail."""

    import concurrent.futures

    async def _log() -> None:
        await audit_logger.log_action(
            user_id=admin_user_id,
            action=f"{action}_csrf_failed",
            resource_type="bulk_operation",
            resource_id=None,
            outcome="denied",
            details={"reason": "csrf_validation_failed"},
        )

    try:
        _ = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            pool.submit(asyncio.run, _log()).result(timeout=5.0)
    except RuntimeError:
        asyncio.run(_log())


def _bulk_change_roles_sync(
    db_pool: Any,
    user_ids: list[str],
    new_role: str,
    admin_user_id: str,
    audit_logger: AuditLogger,
    reason: str,
) -> dict[str, tuple[bool, str]]:
    return _run_async(
        bulk_change_roles(db_pool, user_ids, new_role, admin_user_id, audit_logger, reason)
    )


def _bulk_grant_strategy_sync(
    db_pool: Any,
    user_ids: list[str],
    strategy_id: str,
    admin_user_id: str,
    audit_logger: AuditLogger,
) -> dict[str, tuple[bool, str]]:
    return _run_async(
        bulk_grant_strategy(db_pool, user_ids, strategy_id, admin_user_id, audit_logger)
    )


def _bulk_revoke_strategy_sync(
    db_pool: Any,
    user_ids: list[str],
    strategy_id: str,
    admin_user_id: str,
    audit_logger: AuditLogger,
) -> dict[str, tuple[bool, str]]:
    return _run_async(
        bulk_revoke_strategy(db_pool, user_ids, strategy_id, admin_user_id, audit_logger)
    )


def _list_strategies_sync(db_pool: Any) -> list[StrategyInfo]:
    return _run_async(list_strategies(db_pool))


__all__ = [
    "render_bulk_role_change",
    "render_bulk_strategy_operations",
]
