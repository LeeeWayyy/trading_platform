"""Active notebook sessions table component."""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd
import streamlit as st

from apps.web_console.services.notebook_launcher_service import NotebookSession


def render_active_sessions_table(
    sessions: list[NotebookSession],
    *,
    on_terminate: Callable[[str], bool] | None = None,
) -> None:
    """Render active notebook sessions with a terminate action."""

    st.subheader("Active Sessions")

    if not sessions:
        st.info("No active notebook sessions.")
        empty = pd.DataFrame(columns=["Session ID", "Template", "Status", "Access URL", "Action"])
        st.dataframe(empty, use_container_width=True)
        return

    header_cols = st.columns([2, 2, 2, 2, 1])
    header_cols[0].markdown("**Session ID**")
    header_cols[1].markdown("**Template**")
    header_cols[2].markdown("**Status**")
    header_cols[3].markdown("**Access URL**")
    header_cols[4].markdown("**Action**")

    for session in sessions:
        cols = st.columns([2, 2, 2, 2, 1])
        cols[0].write(session.session_id)
        cols[1].write(session.template_id)
        cols[2].write(session.status.value)
        cols[3].write(session.access_url or "-")

        if on_terminate is None:
            cols[4].write("-")
            continue

        if cols[4].button("Terminate", key=f"terminate_{session.session_id}"):
            success = on_terminate(session.session_id)
            if success:
                st.success(f"Terminated session {session.session_id}.")
                st.rerun()  # Refresh table to show updated status
            else:
                st.error(f"Failed to terminate session {session.session_id}.")


__all__ = ["render_active_sessions_table"]
