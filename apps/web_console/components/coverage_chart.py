"""Coverage gap visualization component."""

from __future__ import annotations

from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

from apps.web_console.components.dataset_helpers import load_quality_datasets
from apps.web_console.services.data_quality_service import DataQualityService
from libs.common.async_utils import run_async

_FETCH_TIMEOUT_SECONDS = 10.0


def render_coverage_chart(service: DataQualityService, user: Any) -> None:
    """Render coverage gap chart derived from validation results."""

    st.subheader("Coverage Gaps")

    dataset_options = load_quality_datasets(service, user)
    if not dataset_options:
        st.info("No datasets available for coverage chart.")
        return

    dataset = st.selectbox("Dataset", options=dataset_options)
    limit = st.slider("Results to scan", min_value=10, max_value=200, value=50, step=10)

    if st.button("Load Coverage Gaps", type="primary"):
        try:
            with st.spinner("Loading validation data..."):
                results = run_async(
                    service.get_validation_results(
                        user=user,
                        dataset=dataset,
                        limit=limit,
                    ),
                    timeout=_FETCH_TIMEOUT_SECONDS,
                )
        except Exception as exc:
            st.error(f"Failed to load coverage data: {exc}")
            return

        rows = []
        for result in results:
            expected = _to_float(result.expected_value)
            actual = _to_float(result.actual_value)
            if expected is None or actual is None:
                continue
            rows.append(
                {
                    "date": result.created_at,
                    "validation": result.validation_type,
                    "gap": expected - actual,
                }
            )

        if not rows:
            st.info("No numeric coverage gaps available in validation results.")
            return

        df = pd.DataFrame(rows)
        fig = px.bar(df, x="date", y="gap", color="validation")
        fig.update_layout(height=320, xaxis_title="Date", yaxis_title="Gap")
        st.plotly_chart(fig, use_container_width=True)


def _to_float(value: object) -> float | None:
    """Safely convert a value to float."""

    try:
        return float(value)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return None


__all__ = ["render_coverage_chart"]
