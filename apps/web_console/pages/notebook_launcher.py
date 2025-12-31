"""Research notebook launcher page."""

from __future__ import annotations

import streamlit as st

from apps.web_console.auth import get_current_user
from apps.web_console.auth.permissions import Permission, has_permission
from apps.web_console.auth.streamlit_helpers import requires_auth
from apps.web_console.components.active_sessions_table import render_active_sessions_table
from apps.web_console.components.notebook_parameters_form import render_notebook_parameters_form
from apps.web_console.components.notebook_template_selector import render_notebook_template_selector
from apps.web_console.services.notebook_launcher_service import (
    NotebookLauncherService,
    SessionStatus,
)


def _get_service(user: dict[str, object]) -> NotebookLauncherService:
    if "notebook_launcher_sessions" not in st.session_state:
        st.session_state["notebook_launcher_sessions"] = {}
    return NotebookLauncherService(
        user=dict(user),
        session_store=st.session_state["notebook_launcher_sessions"],
    )


@requires_auth
def main() -> None:
    """Render the notebook launcher page."""

    st.set_page_config(page_title="Research Notebook Launcher", page_icon="📓", layout="wide")
    st.title("Research Notebook Launcher")

    user = get_current_user()

    if not has_permission(user, Permission.LAUNCH_NOTEBOOKS):
        st.error("Permission denied: LAUNCH_NOTEBOOKS required.")
        st.stop()

    service = _get_service(user)

    try:
        templates = service.list_templates()
    except (PermissionError, ValueError, RuntimeError) as exc:
        st.error(f"Failed to load notebook templates: {exc}")
        return

    template = render_notebook_template_selector(templates)
    if template is None:
        return

    parameters = render_notebook_parameters_form(template)

    if st.button("Launch Notebook", type="primary"):
        try:
            session = service.create_notebook(template.template_id, parameters)
        except Exception as exc:
            st.error(f"Failed to launch notebook: {exc}")
        else:
            if session.status == SessionStatus.ERROR:
                st.error(session.error_message or "Notebook launch failed.")
            else:
                st.success("Notebook session started.")
                if session.access_url:
                    st.write(f"Access URL: {session.access_url}")

    st.divider()

    try:
        sessions = service.list_sessions(include_stopped=False)
    except (PermissionError, ValueError, RuntimeError) as exc:
        st.error(f"Failed to load active sessions: {exc}")
        return

    render_active_sessions_table(sessions, on_terminate=service.terminate_session)


if __name__ == "__main__":
    main()
