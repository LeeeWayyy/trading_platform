"""Sync logs viewer with dataset and level filters."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
import streamlit as st

from apps.web_console.services.data_sync_service import DataSyncService
from libs.common.async_utils import run_async

_FETCH_TIMEOUT_SECONDS = 10.0


def render_sync_logs_viewer(service: DataSyncService, user: Any) -> None:
    """Render recent sync logs with filters."""

    st.subheader("Sync Logs")

    datasets = _load_dataset_options(service, user)
    if datasets is None:
        return

    with st.form("sync_logs_filters"):
        dataset = st.selectbox("Dataset", options=["All"] + datasets)
        level = st.selectbox("Level", options=["All", "info", "warning", "error"])
        limit = st.slider("Max entries", min_value=10, max_value=200, value=50, step=10)
        submitted = st.form_submit_button("Load Logs", type="primary")

    if not submitted:
        st.info("Apply filters to load sync logs.")
        return

    dataset_filter = None if dataset == "All" else dataset
    level_filter = None if level == "All" else level

    try:
        with st.spinner("Loading sync logs..."):
            logs = run_async(
                service.get_sync_logs(
                    user=user,
                    dataset=dataset_filter,
                    level=level_filter,
                    limit=limit,
                ),
                timeout=_FETCH_TIMEOUT_SECONDS,
            )
    except Exception as exc:
        st.error(f"Failed to load sync logs: {exc}")
        return

    if not logs:
        st.info("No sync logs found for the selected filters.")
        return

    rows = [
        {
            "Time": _format_dt(log.created_at),
            "Dataset": log.dataset,
            "Level": log.level.upper(),
            "Message": log.message,
            "Run ID": log.sync_run_id or "-",
        }
        for log in logs
    ]

    st.dataframe(pd.DataFrame(rows), use_container_width=True)

    with st.expander("Log details"):
        for log in logs:
            st.markdown(f"**{log.dataset}** · {log.level.upper()} · {_format_dt(log.created_at)}")
            st.write(log.message)
            if log.extra:
                st.json(log.extra)
            st.divider()


def _load_dataset_options(service: DataSyncService, user: Any) -> list[str] | None:
    try:
        with st.spinner("Loading datasets..."):
            statuses = run_async(service.get_sync_status(user), timeout=_FETCH_TIMEOUT_SECONDS)
    except Exception as exc:
        st.error(f"Failed to load dataset options: {exc}")
        return None

    datasets = sorted({status.dataset for status in statuses})
    if not datasets:
        st.info("No datasets available for sync logs.")
        return None

    return datasets


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M:%S")


__all__ = ["render_sync_logs_viewer"]
