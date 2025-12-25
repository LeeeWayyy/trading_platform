"""SQL query editor component for dataset exploration."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from apps.web_console.services.data_explorer_service import DataExplorerService
from libs.common.async_utils import run_async

_FETCH_TIMEOUT_SECONDS = 15.0


def render_query_editor(service: DataExplorerService, user: Any) -> None:
    """Render SQL query editor and results table."""

    st.subheader("SQL Query")

    datasets = _load_datasets(service, user)
    if not datasets:
        return

    dataset = st.selectbox("Dataset", options=datasets)
    default_query = st.session_state.get("query_editor_default", "SELECT * FROM table LIMIT 100")
    query = st.text_area("SQL Query", value=default_query, height=160)

    if st.button("Run Query", type="primary"):
        st.session_state["query_editor_default"] = query
        try:
            with st.spinner("Executing query..."):
                result = run_async(
                    service.execute_query(user=user, dataset=dataset, query=query),
                    timeout=_FETCH_TIMEOUT_SECONDS,
                )
        except Exception as exc:
            st.error(f"Query failed: {exc}")
            return

        st.caption(f"Rows: {result.total_count} · Has more: {'Yes' if result.has_more else 'No'}")

        if not result.rows:
            st.info("No rows returned.")
            return

        df = pd.DataFrame(result.rows, columns=result.columns or None)
        st.dataframe(df, use_container_width=True)


def _load_datasets(service: DataExplorerService, user: Any) -> list[str]:
    try:
        with st.spinner("Loading datasets..."):
            datasets = run_async(service.list_datasets(user), timeout=_FETCH_TIMEOUT_SECONDS)
    except Exception as exc:
        st.error(f"Failed to load datasets: {exc}")
        return []

    names = [dataset.name for dataset in datasets]
    if not names:
        st.info("No datasets available for querying.")
    return names


__all__ = ["render_query_editor"]
