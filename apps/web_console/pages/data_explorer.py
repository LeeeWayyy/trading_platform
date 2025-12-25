"""Dataset Explorer page (T8.2)."""

from __future__ import annotations

from typing import cast

import streamlit as st

from apps.web_console.auth import get_current_user
from apps.web_console.auth.permissions import Permission, has_permission
from apps.web_console.auth.streamlit_helpers import requires_auth
from apps.web_console.components.coverage_timeline import render_coverage_timeline
from apps.web_console.components.data_preview import render_data_preview
from apps.web_console.components.dataset_browser import render_dataset_browser
from apps.web_console.components.export_dialog import render_export_dialog
from apps.web_console.components.query_editor import render_query_editor
from apps.web_console.components.schema_viewer import render_schema_viewer
from apps.web_console.services.data_explorer_service import DataExplorerService


def _get_data_explorer_service() -> DataExplorerService:
    if "data_explorer_service" not in st.session_state:
        st.session_state["data_explorer_service"] = DataExplorerService()
    return cast(DataExplorerService, st.session_state["data_explorer_service"])


def render_dataset_explorer(user: dict[str, object]) -> None:
    st.title("Dataset Explorer")

    if not has_permission(user, Permission.VIEW_DATA_SYNC):
        st.error("Permission denied: VIEW_DATA_SYNC required")
        st.stop()

    service = _get_data_explorer_service()

    with st.sidebar:
        st.subheader("Datasets")
        render_dataset_browser(service, user)

    render_schema_viewer(service, user)
    st.divider()
    render_data_preview(service, user)
    st.divider()
    render_query_editor(service, user)
    st.divider()
    render_export_dialog(service, user)
    st.divider()
    render_coverage_timeline(service, user)


@requires_auth
def main() -> None:
    st.set_page_config(page_title="Dataset Explorer", page_icon="🗂️", layout="wide")
    user = get_current_user()
    render_dataset_explorer(user)


if __name__ == "__main__":
    main()
