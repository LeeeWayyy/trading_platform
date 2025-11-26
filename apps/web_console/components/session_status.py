"""Session status UI component for OAuth2 timeout warnings.

Displays idle timeout countdown and provides extend session button.
"""

import logging

import streamlit as st

from apps.web_console.auth.idle_timeout_monitor import (
    extend_session_sync,
    get_time_until_idle_timeout,
    should_show_idle_warning,
)

logger = logging.getLogger(__name__)


def render_session_status() -> None:
    """Render session status indicator in sidebar.

    Shows:
    - Idle timeout countdown (when <2 minutes remaining)
    - Extend session button
    - Session expiry time

    Uses idle_timeout_monitor from Component 3 for timeout logic.
    """
    # CRITICAL FIX (Codex High #1): Use LIVE last_activity from st.session_state,
    # not stale user_info dict which is only set once during initial OAuth validation.
    # _check_session_timeout updates st.session_state["last_activity"] on every rerun,
    # so we must use that to avoid false expiry warnings during active use.
    last_activity = st.session_state.get("last_activity")

    if not last_activity:
        # OAuth2 session not fully initialized yet
        return

    # Convert datetime to ISO string for idle_timeout_monitor functions
    last_activity_str = last_activity.isoformat()

    # Check if idle warning should be shown
    if should_show_idle_warning(last_activity_str):
        time_remaining = get_time_until_idle_timeout(last_activity_str)
        minutes_remaining = int(time_remaining.total_seconds() / 60)
        seconds_remaining = int(time_remaining.total_seconds() % 60)

        st.warning(
            f"⏰ **Session expiring soon**\n\n"
            f"{minutes_remaining}m {seconds_remaining}s remaining\n\n"
            "Interact with the page or click below to extend."
        )

        if st.button("Extend Session", use_container_width=True, type="primary"):
            with st.spinner("Extending session..."):
                extend_session_sync()
    else:
        # Session healthy - show subtle status
        time_remaining = get_time_until_idle_timeout(last_activity_str)
        minutes_remaining = int(time_remaining.total_seconds() / 60)

        st.caption(f"✅ Session active ({minutes_remaining}m remaining)")
