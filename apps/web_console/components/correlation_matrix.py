"""Correlation matrix visualization for strategy comparison."""

from __future__ import annotations

import pandas as pd
import plotly.express as px
import streamlit as st


def render_correlation_heatmap(corr_matrix: pd.DataFrame) -> None:
    """Render correlation heatmap."""
    st.subheader("Return Correlation")
    if corr_matrix is None or corr_matrix.empty:
        st.info("Not enough data to compute correlations.")
        return

    fig = px.imshow(
        corr_matrix,
        text_auto=".2f",
        color_continuous_scale="RdYlGn",
        zmin=-1,
        zmax=1,
        aspect="auto",
        origin="lower",
    )
    fig.update_layout(margin=dict(l=40, r=40, t=40, b=40))
    st.plotly_chart(fig, use_container_width=True)


__all__ = ["render_correlation_heatmap"]
