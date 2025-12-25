"""Dataset data preview component."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from apps.web_console.components.dataset_helpers import load_user_datasets
from apps.web_console.services.data_explorer_service import DataExplorerService
from libs.common.async_utils import run_async

_FETCH_TIMEOUT_SECONDS = 10.0


def render_data_preview(service: DataExplorerService, user: Any) -> None:
    """Render first N rows preview for a dataset."""

    st.subheader("Data Preview")

    datasets = load_user_datasets(service, user)
    if not datasets:
        st.info("No datasets available for preview.")
        return

    dataset = st.selectbox("Dataset", options=datasets)
    limit = st.slider("Rows", min_value=10, max_value=1000, value=100, step=10)

    if st.button("Load Preview", type="primary"):
        try:
            with st.spinner("Loading preview..."):
                preview = run_async(
                    service.get_dataset_preview(user=user, dataset=dataset, limit=limit),
                    timeout=_FETCH_TIMEOUT_SECONDS,
                )
        except Exception as exc:
            st.error(f"Failed to load preview: {exc}")
            return

        if not preview.rows:
            st.info("No rows returned for this preview.")
            return

        df = pd.DataFrame(preview.rows, columns=preview.columns or None)
        st.dataframe(df, use_container_width=True)
        st.caption(f"Total rows in dataset: {preview.total_count}")


__all__ = ["render_data_preview"]
