"""Signal correlation matrix visualization component."""

from __future__ import annotations

import plotly.graph_objects as go
import polars as pl
import streamlit as st


def render_correlation_matrix(corr_matrix: pl.DataFrame) -> None:
    """Render signal correlation matrix heatmap.

    Uses go.Figure with go.Heatmap for consistent charting API.

    Args:
        corr_matrix: Polars DataFrame with first column 'signal' and remaining columns as signals
    """
    if corr_matrix.is_empty():
        st.info("Not enough data to compute correlations.")
        return

    if "signal" not in corr_matrix.columns:
        st.error("Correlation matrix missing 'signal' column.")
        return

    corr_pd = corr_matrix.to_pandas()
    signal_names = corr_pd["signal"].tolist()
    corr_pd = corr_pd.set_index("signal")

    # Use go.Figure with go.Heatmap for consistent charting API
    fig = go.Figure(
        data=go.Heatmap(
            z=corr_pd.values,
            x=signal_names,
            y=signal_names,
            colorscale="RdYlGn",
            zmin=-1,
            zmax=1,
            text=[[f"{v:.2f}" for v in row] for row in corr_pd.values],
            texttemplate="%{text}",
            hovertemplate="Signal: %{x}<br>Signal: %{y}<br>Correlation: %{z:.2f}<extra></extra>",
        )
    )
    fig.update_layout(
        margin={"l": 40, "r": 40, "t": 40, "b": 40},
        xaxis_title="Signal",
        yaxis_title="Signal",
    )
    st.plotly_chart(fig, use_container_width=True)


__all__ = ["render_correlation_matrix"]
