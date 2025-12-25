"""Dataset sync status table component."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
import streamlit as st

from apps.web_console.services.data_sync_service import DataSyncService
from libs.common.async_utils import run_async

_FETCH_TIMEOUT_SECONDS = 10.0


def render_sync_status_table(service: DataSyncService, user: Any) -> None:
    """Render dataset sync status table."""

    st.subheader("Dataset Sync Status")

    try:
        with st.spinner("Loading sync status..."):
            statuses = run_async(service.get_sync_status(user), timeout=_FETCH_TIMEOUT_SECONDS)
    except Exception as exc:
        st.error(f"Failed to load sync status: {exc}")
        return

    if not statuses:
        st.info("No sync status available for your datasets.")
        return

    rows = [
        {
            "Dataset": status.dataset,
            "Last Sync": _format_dt(status.last_sync),
            "Row Count": status.row_count if status.row_count is not None else "-",
            "Validation": status.validation_status or "-",
            "Schema Version": status.schema_version or "-",
        }
        for status in statuses
    ]

    st.dataframe(pd.DataFrame(rows), use_container_width=True)


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M:%S")


__all__ = ["render_sync_status_table"]
