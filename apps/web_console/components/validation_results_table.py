"""Validation results table component."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
import streamlit as st

from apps.web_console.services.data_quality_service import DataQualityService
from libs.common.async_utils import run_async

_FETCH_TIMEOUT_SECONDS = 10.0


def render_validation_results_table(service: DataQualityService, user: Any) -> None:
    """Render validation run results with filters."""

    st.subheader("Validation Results")

    with st.form("validation_filters"):
        dataset = st.text_input("Dataset (optional)")
        limit = st.slider("Max results", min_value=10, max_value=200, value=50, step=10)
        submitted = st.form_submit_button("Load Results", type="primary")

    if not submitted:
        st.info("Apply filters to load validation results.")
        return

    dataset_filter = dataset.strip() or None

    try:
        with st.spinner("Loading validation results..."):
            results = run_async(
                service.get_validation_results(
                    user=user,
                    dataset=dataset_filter,
                    limit=limit,
                ),
                timeout=_FETCH_TIMEOUT_SECONDS,
            )
    except Exception as exc:
        st.error(f"Failed to load validation results: {exc}")
        return

    if not results:
        st.info("No validation results found for the selected filters.")
        return

    rows = [
        {
            "Time": _format_dt(result.created_at),
            "Dataset": result.dataset,
            "Run ID": result.sync_run_id or "-",
            "Check": result.validation_type,
            "Status": result.status,
            "Expected": result.expected_value if result.expected_value is not None else "-",
            "Actual": result.actual_value if result.actual_value is not None else "-",
            "Error": result.error_message or "-",
        }
        for result in results
    ]

    st.dataframe(pd.DataFrame(rows), use_container_width=True)


def _format_dt(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.strftime("%Y-%m-%d %H:%M:%S")


__all__ = ["render_validation_results_table"]
