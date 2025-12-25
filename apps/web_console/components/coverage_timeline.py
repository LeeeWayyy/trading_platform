"""Coverage timeline visualization for dataset ranges."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from apps.web_console.services.data_explorer_service import DataExplorerService
from libs.common.async_utils import run_async

_FETCH_TIMEOUT_SECONDS = 10.0


def render_coverage_timeline(service: DataExplorerService, user: Any) -> None:
    """Render dataset coverage timeline chart."""

    st.subheader("Coverage Timeline")

    try:
        with st.spinner("Loading datasets..."):
            datasets = run_async(service.list_datasets(user), timeout=_FETCH_TIMEOUT_SECONDS)
    except Exception as exc:
        st.error(f"Failed to load datasets: {exc}")
        return

    if not datasets:
        st.info("No datasets available for coverage timeline.")
        return

    timeline_rows = []
    for dataset in datasets:
        date_range = dataset.date_range or {}
        start = _parse_date(date_range.get("start"))
        end = _parse_date(date_range.get("end"))
        if not start or not end:
            continue
        timeline_rows.append(
            {
                "dataset": dataset.name,
                "start": start,
                "end": end,
            }
        )

    if not timeline_rows:
        st.info("No coverage ranges available to display.")
        return

    df = pd.DataFrame(timeline_rows)
    fig = px.timeline(
        df,
        x_start="start",
        x_end="end",
        y="dataset",
        title=None,
    )
    fig.update_layout(
        xaxis_title="Date",
        yaxis_title="Dataset",
        height=300 + len(df) * 20,
    )
    st.plotly_chart(fig, use_container_width=True)


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


__all__ = ["render_coverage_timeline"]
