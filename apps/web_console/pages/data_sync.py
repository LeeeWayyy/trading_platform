"""Data Sync Dashboard page (T8.1)."""

from __future__ import annotations

import logging
from typing import cast

import streamlit as st

from apps.web_console.auth import get_current_user
from apps.web_console.auth.permissions import Permission, has_permission
from apps.web_console.auth.streamlit_helpers import requires_auth
from apps.web_console.components.sync_logs_viewer import render_sync_logs_viewer
from apps.web_console.components.sync_schedule_editor import render_sync_schedule_editor
from apps.web_console.components.sync_status_table import render_sync_status_table
from apps.web_console.services.data_sync_service import DataSyncService, RateLimitExceeded
from libs.common.async_utils import run_async

logger = logging.getLogger(__name__)

_FETCH_TIMEOUT_SECONDS = 10.0
_TRIGGER_TIMEOUT_SECONDS = 10.0


def _get_data_sync_service() -> DataSyncService:
    if "data_sync_service" not in st.session_state:
        st.session_state["data_sync_service"] = DataSyncService()
    return cast(DataSyncService, st.session_state["data_sync_service"])


def _render_manual_sync_sidebar(service: DataSyncService, user: dict[str, object]) -> None:
    st.sidebar.subheader("Manual Sync")

    if not has_permission(user, Permission.TRIGGER_DATA_SYNC):
        st.sidebar.info("Permission required: TRIGGER_DATA_SYNC")
        return

    try:
        with st.spinner("Loading datasets..."):
            statuses = run_async(
                service.get_sync_status(user),
                timeout=_FETCH_TIMEOUT_SECONDS,
            )
    except Exception as exc:  # pragma: no cover - UI feedback
        st.sidebar.error(f"Failed to load datasets: {exc}")
        return

    datasets = sorted({status.dataset for status in statuses})
    if not datasets:
        st.sidebar.info("No datasets available for manual sync.")
        return

    with st.sidebar.form("manual_sync_form"):
        dataset = st.selectbox("Dataset", options=datasets, key="manual_sync_dataset")
        reason = st.text_input(
            "Reason",
            value="",
            placeholder="Why run this sync now?",
            key="manual_sync_reason",
        )
        submitted = st.form_submit_button("Trigger Sync", type="primary")

    if not submitted:
        return

    if not reason.strip():
        st.sidebar.warning("Please provide a reason for audit logging.")
        return

    try:
        with st.spinner("Triggering sync..."):
            job = run_async(
                service.trigger_sync(user=user, dataset=dataset, reason=reason.strip()),
                timeout=_TRIGGER_TIMEOUT_SECONDS,
            )
    except RateLimitExceeded as exc:
        st.sidebar.error(str(exc))
        return
    except Exception as exc:  # pragma: no cover - UI feedback
        st.sidebar.error(f"Failed to trigger sync: {exc}")
        return

    st.sidebar.success(f"Sync queued: {job.id}")


def render_data_sync_dashboard(user: dict[str, object]) -> None:
    st.title("Data Sync Dashboard")

    if not has_permission(user, Permission.VIEW_DATA_SYNC):
        st.error("Permission denied: VIEW_DATA_SYNC required")
        st.stop()

    service = _get_data_sync_service()
    _render_manual_sync_sidebar(service, user)

    tab1, tab2, tab3 = st.tabs(["Sync Status", "Sync Logs", "Schedule Config"])

    with tab1:
        render_sync_status_table(service, user)

    with tab2:
        render_sync_logs_viewer(service, user)

    with tab3:
        render_sync_schedule_editor(service, user)


@requires_auth
def main() -> None:
    st.set_page_config(page_title="Data Sync Dashboard", page_icon="🔄", layout="wide")
    user = get_current_user()
    render_data_sync_dashboard(user)


if __name__ == "__main__":
    main()
