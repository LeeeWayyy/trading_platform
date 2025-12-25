"""SQL query editor component for dataset exploration."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from apps.web_console.components.dataset_helpers import load_user_datasets
from apps.web_console.services.data_explorer_service import DataExplorerService
from libs.common.async_utils import run_async

_FETCH_TIMEOUT_SECONDS = 15.0


def render_query_editor(service: DataExplorerService, user: Any) -> None:
    """Render SQL query editor and results table."""

    st.subheader("SQL Query")

    datasets = load_user_datasets(service, user)
    if not datasets:
        st.info("No datasets available for querying.")
        return

    dataset = st.selectbox("Dataset", options=datasets)
    default_query = st.session_state.get(
        "query_editor_default", "-- Example: SELECT * FROM crsp_daily LIMIT 100"
    )
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


__all__ = ["render_query_editor"]
