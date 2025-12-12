"""Strategy assignment component for user management.

[v1.1] CSRF failures logged to audit trail.
[v1.1] Confirmation dialog before executing changes.
[v1.1] CSRF rotation on both success and failure.
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
    get_user_strategies,
    grant_strategy,
    list_strategies,
    revoke_strategy,
)

logger = logging.getLogger(__name__)


def render_strategy_assignment(
    user_id: str,
    admin_user_id: str,
    db_pool: Any,
    audit_logger: Any,
) -> None:
    """Render strategy assignment UI for a single user."""

    confirm_key = f"confirm_strategy_{user_id}"

    st.subheader(f"Strategy Access: {user_id}")

    # Fetch current assignments and available strategies
    all_strategies = _list_strategies_sync(db_pool)
    current_strategies = _get_user_strategies_sync(db_pool, user_id)

    if not all_strategies:
        st.info("No strategies configured. Add strategies via CLI first.")
        return

    # Build selection options
    strategy_options = {s.strategy_id: f"{s.name} ({s.strategy_id})" for s in all_strategies}

    csrf_token = generate_csrf_token()

    with st.form(f"strategy_form_{user_id}"):
        selected = st.multiselect(
            "Assigned Strategies",
            options=list(strategy_options.keys()),
            default=current_strategies,
            format_func=lambda x: strategy_options.get(x, x),
            key=f"strategies_{user_id}",
        )

        # Hidden CSRF
        submitted_csrf = st.text_input(
            "csrf",
            value=csrf_token,
            type="password",
            label_visibility="hidden",
            key=f"strategy_csrf_{user_id}",
        )

        submitted = st.form_submit_button("Update Strategies", type="primary")

        if submitted:
            if not verify_csrf_token(submitted_csrf):
                st.error("Invalid form submission. Please refresh.")
                # [v1.1] Log CSRF failure to audit trail
                _log_csrf_failure_sync(
                    audit_logger, admin_user_id, "strategy_assignment", user_id
                )
                rotate_csrf_token()  # [v1.1] Rotate on failure
                return

            # Calculate grants and revokes
            current_set = set(current_strategies)
            selected_set = set(selected)

            to_grant = selected_set - current_set
            to_revoke = current_set - selected_set

            if not to_grant and not to_revoke:
                st.info("No changes to apply.")
                return

            # [v1.1] Set confirmation pending - store CSRF for execution-time verification
            st.session_state[confirm_key] = {
                "to_grant": list(to_grant),
                "to_revoke": list(to_revoke),
                "csrf_token": submitted_csrf,  # [v1.3] Store for execution-time verification
            }
            st.rerun()

    # [v1.1] Confirmation dialog
    if st.session_state.get(confirm_key):
        pending = st.session_state[confirm_key]
        changes_summary = []
        if pending["to_grant"]:
            changes_summary.append(f"Grant: {', '.join(pending['to_grant'])}")
        if pending["to_revoke"]:
            changes_summary.append(f"Revoke: {', '.join(pending['to_revoke'])}")

        st.warning(
            f"**Confirm Strategy Changes for {user_id}**\n\n"
            + "\n\n".join(changes_summary)
            + "\n\n**This will invalidate the user's active sessions.**"
        )

        col1, col2 = st.columns(2)
        with col1:
            if st.button("Confirm", key=f"confirm_strat_{user_id}", type="primary"):
                # [v1.3] Re-verify CSRF at execution time
                if not verify_csrf_token(pending.get("csrf_token", "")):
                    st.error("Session expired. Please refresh and try again.")
                    _log_csrf_failure_sync(
                        audit_logger, admin_user_id, "strategy_assignment_execute", user_id
                    )
                    del st.session_state[confirm_key]
                    rotate_csrf_token()
                    return

                results = []
                for strategy_id in pending["to_grant"]:
                    success, msg = _grant_strategy_sync(
                        db_pool, user_id, strategy_id, admin_user_id, audit_logger
                    )
                    results.append((strategy_id, "grant", success, msg))

                for strategy_id in pending["to_revoke"]:
                    success, msg = _revoke_strategy_sync(
                        db_pool, user_id, strategy_id, admin_user_id, audit_logger
                    )
                    results.append((strategy_id, "revoke", success, msg))

                # Show results
                for _strategy_id, action, success, msg in results:
                    if success:
                        st.success(f"{action.title()}: {msg}")
                    else:
                        st.error(f"{action.title()} failed: {msg}")

                rotate_csrf_token()
                del st.session_state[confirm_key]
                st.rerun()

        with col2:
            if st.button("Cancel", key=f"cancel_strat_{user_id}"):
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
            resource_type="user_strategy",
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


T = TypeVar("T")


def _run_async(coro: Coroutine[Any, Any, T]) -> T:
    """Run async coroutine from sync context."""

    import concurrent.futures

    try:
        _ = asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor() as pool:
            return pool.submit(asyncio.run, coro).result(timeout=10.0)
    except RuntimeError:
        return asyncio.run(coro)


# Sync wrappers for async functions
def _list_strategies_sync(db_pool: Any) -> list[StrategyInfo]:
    return _run_async(list_strategies(db_pool))


def _get_user_strategies_sync(db_pool: Any, user_id: str) -> list[str]:
    return _run_async(get_user_strategies(db_pool, user_id))


def _grant_strategy_sync(
    db_pool: Any,
    user_id: str,
    strategy_id: str,
    admin_user_id: str,
    audit_logger: AuditLogger,
) -> tuple[bool, str]:
    return _run_async(
        grant_strategy(db_pool, user_id, strategy_id, admin_user_id, audit_logger)
    )


def _revoke_strategy_sync(
    db_pool: Any,
    user_id: str,
    strategy_id: str,
    admin_user_id: str,
    audit_logger: AuditLogger,
) -> tuple[bool, str]:
    return _run_async(
        revoke_strategy(db_pool, user_id, strategy_id, admin_user_id, audit_logger)
    )


__all__ = [
    "render_strategy_assignment",
]
