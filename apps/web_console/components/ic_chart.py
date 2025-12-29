"""IC time-series visualization component."""

from __future__ import annotations

import plotly.graph_objects as go
import polars as pl
import streamlit as st


def render_ic_chart(daily_ic: pl.DataFrame) -> None:
    """Render IC time-series chart with Pearson and Rank IC.

    Args:
        daily_ic: DataFrame with columns [date, ic, rank_ic, rolling_ic_20d]
    """
    if daily_ic.is_empty():
        st.info("No IC data available for this signal.")
        return

    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=daily_ic["date"].to_list(),
            y=daily_ic["rank_ic"].to_list(),
            name="Rank IC",
            mode="lines",
            line={"color": "blue", "width": 1},
            opacity=0.5,
        )
    )

    if "rolling_ic_20d" in daily_ic.columns:
        fig.add_trace(
            go.Scatter(
                x=daily_ic["date"].to_list(),
                y=daily_ic["rolling_ic_20d"].to_list(),
                name="Rolling 20d Rank IC",
                mode="lines",
                line={"color": "blue", "width": 2},
            )
        )

    fig.add_trace(
        go.Scatter(
            x=daily_ic["date"].to_list(),
            y=daily_ic["ic"].to_list(),
            name="Pearson IC",
            mode="lines",
            line={"color": "gray", "width": 1, "dash": "dot"},
            opacity=0.5,
        )
    )

    fig.add_hline(y=0, line_dash="dash", line_color="gray")

    fig.update_layout(
        title="Information Coefficient Over Time",
        xaxis_title="Date",
        yaxis_title="IC",
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "xanchor": "right", "x": 1},
        height=400,
    )

    st.plotly_chart(fig, use_container_width=True)


__all__ = ["render_ic_chart"]
