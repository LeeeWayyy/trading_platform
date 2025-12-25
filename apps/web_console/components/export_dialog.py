"""Export dialog component for dataset queries."""

from __future__ import annotations

from typing import Any, Literal, cast

import streamlit as st

from apps.web_console.services.data_explorer_service import DataExplorerService
from libs.common.async_utils import run_async

_FETCH_TIMEOUT_SECONDS = 15.0


def render_export_dialog(service: DataExplorerService, user: Any) -> None:
    """Render export form for query results."""

    st.subheader("Export Data")

    datasets = _load_datasets(service, user)
    if not datasets:
        return

    dataset = st.selectbox("Dataset", options=datasets)
    query = st.text_area("SQL Query", value="SELECT * FROM table LIMIT 1000", height=160)
    format_choice: Literal["csv", "parquet"] = cast(
        Literal["csv", "parquet"], st.selectbox("Format", options=["csv", "parquet"])
    )

    if st.button("Start Export", type="primary"):
        try:
            with st.spinner("Submitting export job..."):
                job = run_async(
                    service.export_data(
                        user=user,
                        dataset=dataset,
                        query=query,
                        format=format_choice,
                    ),
                    timeout=_FETCH_TIMEOUT_SECONDS,
                )
        except Exception as exc:
            st.error(f"Export failed: {exc}")
            return

        st.success("Export job queued.")
        st.json(
            {
                "job_id": job.id,
                "status": job.status,
                "format": job.format,
                "expires_at": job.expires_at.isoformat() if job.expires_at else None,
            }
        )


def _load_datasets(service: DataExplorerService, user: Any) -> list[str]:
    try:
        with st.spinner("Loading datasets..."):
            datasets = run_async(service.list_datasets(user), timeout=_FETCH_TIMEOUT_SECONDS)
    except Exception as exc:
        st.error(f"Failed to load datasets: {exc}")
        return []

    names = [dataset.name for dataset in datasets]
    if not names:
        st.info("No datasets available for export.")
    return names


__all__ = ["render_export_dialog"]
