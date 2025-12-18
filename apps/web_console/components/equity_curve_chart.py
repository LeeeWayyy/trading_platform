"""Equity curve visualization component.

Renders cumulative returns over time as a Plotly line chart.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import plotly.graph_objects as go
import streamlit as st

if TYPE_CHECKING:
    import polars as pl


def render_equity_curve(
    daily_returns: pl.DataFrame,
    title: str = "Equity Curve",
    height: int = 400,
) -> None:
    """Render equity curve chart from daily returns.

    Args:
        daily_returns: DataFrame with columns: date, return
        title: Chart title
        height: Chart height in pixels

    The chart shows cumulative returns computed as:
    (1 + r1) * (1 + r2) * ... * (1 + rn) - 1
    """

    if daily_returns is None or daily_returns.height == 0:
        st.info("No return data available for equity curve")
        return

    # Validate required columns
    required_cols = {"date", "return"}
    if not required_cols.issubset(set(daily_returns.columns)):
        st.error(f"Missing columns for equity curve. Required: {required_cols}")
        return

    try:
        # Sort by date and compute cumulative returns
        sorted_df = daily_returns.sort("date")

        # Cumulative return: (1 + r1) * (1 + r2) * ... - 1
        cumulative = (1 + sorted_df["return"]).cum_prod() - 1

        # Add cumulative column
        chart_df = sorted_df.with_columns(cumulative.alias("cumulative_return"))

        # Convert to pandas for plotly
        chart_pd = chart_df.select(["date", "cumulative_return"]).to_pandas()

        # Create plotly figure
        fig = go.Figure()

        fig.add_trace(
            go.Scatter(
                x=chart_pd["date"],
                y=chart_pd["cumulative_return"] * 100,  # Convert to percentage
                mode="lines",
                name="Cumulative Return",
                line={"color": "#1f77b4", "width": 2},
                fill="tozeroy",
                fillcolor="rgba(31, 119, 180, 0.1)",
            )
        )

        # Add zero line
        fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)

        fig.update_layout(
            title=title,
            xaxis_title="Date",
            yaxis_title="Cumulative Return (%)",
            height=height,
            showlegend=False,
            hovermode="x unified",
            yaxis={"tickformat": ".1f", "ticksuffix": "%"},
        )

        st.plotly_chart(fig, use_container_width=True)

    except Exception as e:
        st.error(f"Failed to render equity curve: {e}")


__all__ = ["render_equity_curve"]
