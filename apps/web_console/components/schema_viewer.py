"""Schema viewer component for dataset tables."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from apps.web_console.services.data_explorer_service import DataExplorerService
from apps.web_console.services.sql_validator import DATASET_TABLES
from libs.common.async_utils import run_async

_FETCH_TIMEOUT_SECONDS = 10.0


def render_schema_viewer(service: DataExplorerService, user: Any) -> None:
    """Render schema viewer based on queryable tables.

    Security: Only shows datasets the user has permission to access.
    """

    st.subheader("Schema Viewer")

    # Filter datasets by user permissions (security: don't leak dataset names)
    allowed_datasets = _load_allowed_datasets(service, user)
    if not allowed_datasets:
        st.info("No datasets available for schema viewing.")
        return

    dataset = st.selectbox("Dataset", options=sorted(allowed_datasets))
    tables = DATASET_TABLES.get(dataset, [])

    if not tables:
        st.info("No tables available for this dataset.")
        return

    table = st.selectbox("Table", options=tables)

    if st.button("Load Schema", type="primary"):
        query = f"SELECT * FROM {table} LIMIT 0"
        try:
            with st.spinner("Loading schema..."):
                result = run_async(
                    service.execute_query(user=user, dataset=dataset, query=query),
                    timeout=_FETCH_TIMEOUT_SECONDS,
                )
        except Exception as exc:
            st.error(f"Failed to load schema: {exc}")
            return

        columns = result.columns or []
        if not columns:
            st.info("No schema metadata returned for this table.")
            return

        df = pd.DataFrame([{"Column": name, "Type": "unknown"} for name in columns])
        st.dataframe(df, use_container_width=True)
        st.caption("Column types are not yet available from the backend.")


def _load_allowed_datasets(service: DataExplorerService, user: Any) -> list[str]:
    """Load datasets filtered by user permissions."""
    try:
        with st.spinner("Loading datasets..."):
            datasets = run_async(service.list_datasets(user), timeout=_FETCH_TIMEOUT_SECONDS)
    except Exception as exc:
        st.error(f"Failed to load datasets: {exc}")
        return []

    # Only include datasets that have queryable tables
    return [ds.name for ds in datasets if ds.name in DATASET_TABLES]


__all__ = ["render_schema_viewer"]
