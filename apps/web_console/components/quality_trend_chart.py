"""Quality trend chart component."""

from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from apps.web_console.components.dataset_helpers import load_quality_datasets
from apps.web_console.services.data_quality_service import DataQualityService
from libs.common.async_utils import run_async

_FETCH_TIMEOUT_SECONDS = 10.0


def render_quality_trend_chart(service: DataQualityService, user: Any) -> None:
    """Render historical quality metrics for a dataset."""

    st.subheader("Quality Trends")

    dataset_options = load_quality_datasets(service, user)
    if not dataset_options:
        st.info("No datasets available for trend chart.")
        return

    dataset = st.selectbox("Dataset", options=dataset_options)
    days = st.slider("Days", min_value=7, max_value=180, value=30, step=7)

    if st.button("Load Trend", type="primary"):
        try:
            with st.spinner("Loading trend data..."):
                trend = run_async(
                    service.get_quality_trends(user=user, dataset=dataset, days=days),
                    timeout=_FETCH_TIMEOUT_SECONDS,
                )
        except Exception as exc:
            st.error(f"Failed to load trend data: {exc}")
            return

        if not trend.data_points:
            st.info("No trend data available for this dataset.")
            return

        df = pd.DataFrame(
            [
                {
                    "date": point.date,
                    "metric": point.metric,
                    "value": point.value,
                }
                for point in trend.data_points
            ]
        )

        fig = px.line(df, x="date", y="value", color="metric")
        fig.update_layout(height=320, xaxis_title="Date", yaxis_title="Value")
        st.plotly_chart(fig, use_container_width=True)


__all__ = ["render_quality_trend_chart"]
