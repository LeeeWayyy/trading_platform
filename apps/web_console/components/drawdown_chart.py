"""Drawdown visualization component.

Renders maximum drawdown over time as a Plotly area chart.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import plotly.graph_objects as go
import streamlit as st

if TYPE_CHECKING:
    import polars as pl


def render_drawdown_chart(
    daily_returns: pl.DataFrame,
    title: str = "Drawdown",
    height: int = 300,
) -> None:
    """Render drawdown chart from daily returns.

    Args:
        daily_returns: DataFrame with columns: date, return
        title: Chart title
        height: Chart height in pixels

    Drawdown is computed as:
    - cumulative = (1 + r).cumprod()
    - running_max = cumulative.cummax()
    - drawdown = (cumulative - running_max) / running_max
    """

    if daily_returns is None or daily_returns.height == 0:
        st.info("No return data available for drawdown chart")
        return

    # Validate required columns
    required_cols = {"date", "return"}
    if not required_cols.issubset(set(daily_returns.columns)):
        st.error(f"Missing columns for drawdown. Required: {required_cols}")
        return

    try:
        # Sort by date
        sorted_df = daily_returns.sort("date")

        # Compute cumulative wealth (1 + cumulative return)
        cumulative = (1 + sorted_df["return"]).cum_prod()

        # Running maximum
        running_max = cumulative.cum_max()

        # Drawdown = (current - peak) / peak
        drawdown = (cumulative - running_max) / running_max

        # Add columns
        chart_df = sorted_df.with_columns(
            [
                cumulative.alias("cumulative"),
                running_max.alias("running_max"),
                drawdown.alias("drawdown"),
            ]
        )

        # Convert to pandas for plotly
        chart_pd = chart_df.select(["date", "drawdown"]).to_pandas()

        # Create plotly figure
        fig = go.Figure()

        fig.add_trace(
            go.Scatter(
                x=chart_pd["date"],
                y=chart_pd["drawdown"] * 100,  # Convert to percentage
                mode="lines",
                name="Drawdown",
                line={"color": "#d62728", "width": 1.5},
                fill="tozeroy",
                fillcolor="rgba(214, 39, 40, 0.3)",
            )
        )

        # Find max drawdown for annotation (guard against empty/NaN series)
        if not chart_pd.empty and not chart_pd["drawdown"].isnull().all():
            max_dd = chart_pd["drawdown"].min()
            max_dd_date = chart_pd.loc[chart_pd["drawdown"].idxmin(), "date"]

            fig.add_annotation(
                x=max_dd_date,
                y=max_dd * 100,
                text=f"Max DD: {max_dd * 100:.1f}%",
                showarrow=True,
                arrowhead=2,
                ax=0,
                ay=-40,
            )

        fig.update_layout(
            title=title,
            xaxis_title="Date",
            yaxis_title="Drawdown (%)",
            height=height,
            showlegend=False,
            hovermode="x unified",
            yaxis={"tickformat": ".1f", "ticksuffix": "%"},
        )

        st.plotly_chart(fig, use_container_width=True)

    except Exception as e:
        st.error(f"Failed to render drawdown chart: {e}")


__all__ = ["render_drawdown_chart"]
