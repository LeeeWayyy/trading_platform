"""User role editor component with CSRF protection and confirmation.

[v1.1] CSRF failures logged to audit trail, not just logger.
[v1.1] Per-request DB pool pattern for thread safety.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import streamlit as st

from apps.web_console.components.csrf_protection import (
    generate_csrf_token,
    rotate_csrf_token,
    verify_csrf_token,
)
from apps.web_console.services.user_management import change_user_role

logger = logging.getLogger(__name__)


def render_role_editor(
    user_id: str,
    current_role: str,
    admin_user_id: str,
    db_pool: Any,
    audit_logger: Any,
) -> None:
    """Render role editor form for a single user."""

    # State keys for this user
    confirm_key = f"confirm_role_{user_id}"

    st.subheader(f"Change Role: {user_id}")
    st.caption(f"Current role: **{current_role}**")

    csrf_token = generate_csrf_token()

    with st.form(f"role_form_{user_id}"):
        new_role = st.selectbox(
            "New Role",
            ["viewer", "operator", "admin"],
            index=["viewer", "operator", "admin"].index(current_role),
            key=f"role_select_{user_id}",
        )

        reason = st.text_area(
            "Reason for change (required)",
            placeholder="Enter justification for role change...",
            key=f"role_reason_{user_id}",
        )

        # Hidden CSRF token (use password type to hide)
        submitted_csrf = st.text_input(
            "csrf",
            value=csrf_token,
            type="password",
            label_visibility="hidden",
            key=f"csrf_{user_id}",
        )

        submitted = st.form_submit_button("Change Role", type="primary")

        if submitted:
            # Verify CSRF
            if not verify_csrf_token(submitted_csrf):
                st.error("Invalid form submission. Please refresh and try again.")
                # [v1.1] Log CSRF failure to audit trail
                _log_csrf_failure_sync(
                    audit_logger,
                    admin_user_id,
                    "role_change",
                    user_id,
                )
                rotate_csrf_token()  # [v1.1] Rotate after failure too
                return

            # Validate reason
            if not reason or len(reason.strip()) < 10:
                st.error("Reason must be at least 10 characters.")
                return

            # Check if role actually changed
            if new_role == current_role:
                st.warning("No change - user already has this role.")
                return

            # Set confirmation pending
            st.session_state[confirm_key] = {
                "new_role": new_role,
                "reason": reason.strip(),
            }
            st.rerun()

    # Confirmation dialog
    if st.session_state.get(confirm_key):
        pending = st.session_state[confirm_key]
        st.warning(
            f"**Confirm Role Change**\n\n"
            f"User: {user_id}\n\n"
            f"From: {current_role} -> To: {pending['new_role']}\n\n"
            f"This will invalidate all active sessions for this user."
        )

        col1, col2 = st.columns(2)
        with col1:
            if st.button("Confirm", key=f"confirm_btn_{user_id}", type="primary"):
                # Execute role change
                success, message = _execute_role_change_sync(
                    db_pool=db_pool,
                    user_id=user_id,
                    new_role=pending["new_role"],
                    admin_user_id=admin_user_id,
                    audit_logger=audit_logger,
                    reason=pending["reason"],
                )

                if success:
                    st.success(message)
                    rotate_csrf_token()
                else:
                    st.error(message)

                del st.session_state[confirm_key]
                st.rerun()

        with col2:
            if st.button("Cancel", key=f"cancel_btn_{user_id}"):
                del st.session_state[confirm_key]
                st.rerun()


def _log_csrf_failure_sync(
    audit_logger: Any,
    admin_user_id: str,
    action: str,
    target_user_id: str,
) -> None:
    """[v1.1] Log CSRF failure to audit trail (sync wrapper)."""

    import concurrent.futures

    async def _log() -> None:
        await audit_logger.log_action(
            user_id=admin_user_id,
            action=f"{action}_csrf_failed",
            resource_type="user",
            resource_id=target_user_id,
            outcome="denied",
            details={"reason": "csrf_validation_failed"},
        )

    try:
        _ = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            pool.submit(asyncio.run, _log()).result(timeout=5.0)
    except RuntimeError:
        asyncio.run(_log())


def _execute_role_change_sync(
    db_pool: Any,
    user_id: str,
    new_role: str,
    admin_user_id: str,
    audit_logger: Any,
    reason: str,
) -> tuple[bool, str]:
    """Sync wrapper for change_user_role.

    [v1.1] Uses per-request pattern - creates fresh pool in thread context.
    """

    import concurrent.futures

    async def _async_change() -> tuple[bool, str]:
        # [v1.1] For thread safety, we use the passed pool which should be
        # created fresh per-request by the caller (admin_users.py)
        return await change_user_role(
            db_pool=db_pool,
            user_id=user_id,
            new_role=new_role,
            admin_user_id=admin_user_id,
            audit_logger=audit_logger,
            reason=reason,
        )

    try:
        _ = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(asyncio.run, _async_change())
            result: tuple[bool, str] = future.result(timeout=10.0)
            return result
    except RuntimeError:
        return asyncio.run(_async_change())


__all__ = [
    "render_role_editor",
]
