"""Dataset catalog browser component."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
import streamlit as st

from apps.web_console.services.data_explorer_service import DataExplorerService
from libs.common.async_utils import run_async

_FETCH_TIMEOUT_SECONDS = 10.0


def render_dataset_browser(service: DataExplorerService, user: Any) -> None:
    """Render dataset catalog with metadata."""

    st.subheader("Dataset Catalog")

    try:
        with st.spinner("Loading datasets..."):
            datasets = run_async(service.list_datasets(user), timeout=_FETCH_TIMEOUT_SECONDS)
    except Exception as exc:
        st.error(f"Failed to load datasets: {exc}")
        return

    if not datasets:
        st.info("No datasets available for your account.")
        return

    rows = []
    for dataset in datasets:
        date_range = dataset.date_range or {}
        rows.append(
            {
                "Dataset": dataset.name,
                "Description": dataset.description or "-",
                "Rows": dataset.row_count if dataset.row_count is not None else "-",
                "Symbols": dataset.symbol_count if dataset.symbol_count is not None else "-",
                "Coverage": _format_date_range(date_range.get("start"), date_range.get("end")),
                "Last Sync": _format_dt(dataset.last_sync),
            }
        )

    st.dataframe(pd.DataFrame(rows), use_container_width=True)


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _format_date_range(start: str | None, end: str | None) -> str:
    if not start and not end:
        return "-"
    return f"{start or '?'} to {end or '?'}"


__all__ = ["render_dataset_browser"]
