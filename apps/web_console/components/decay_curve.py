"""Decay curve visualization component."""

from __future__ import annotations

import plotly.graph_objects as go
import polars as pl
import streamlit as st


def render_decay_curve(decay_curve: pl.DataFrame, half_life: float | None = None) -> None:
    """Render decay curve showing IC at multiple horizons.

    Args:
        decay_curve: DataFrame with columns [horizon, ic, rank_ic]
        half_life: Optional half-life in days for annotation
    """
    if decay_curve.is_empty():
        st.info("No decay curve data available for this signal.")
        return

    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=decay_curve["horizon"].to_list(),
            y=decay_curve["rank_ic"].to_list(),
            name="Rank IC",
            mode="lines+markers",
            line={"color": "blue", "width": 2},
            marker={"size": 8},
        )
    )

    fig.add_trace(
        go.Scatter(
            x=decay_curve["horizon"].to_list(),
            y=decay_curve["ic"].to_list(),
            name="Pearson IC",
            mode="lines+markers",
            line={"color": "gray", "width": 1, "dash": "dot"},
            marker={"size": 6},
        )
    )

    fig.add_hline(y=0, line_dash="dash", line_color="gray")

    if half_life is not None:
        fig.add_vline(x=half_life, line_dash="dot", line_color="red")
        fig.add_annotation(
            x=half_life,
            y=0.5,
            text=f"Half-life: {half_life:.1f}d",
            showarrow=True,
            arrowhead=2,
        )

    fig.update_layout(
        title="Signal Decay Curve",
        xaxis_title="Horizon (days)",
        yaxis_title="IC",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "right", "x": 1},
        height=350,
    )

    st.plotly_chart(fig, use_container_width=True)


__all__ = ["render_decay_curve"]
