"""Time-series visualization for factor exposures."""

from __future__ import annotations

import plotly.graph_objects as go
import polars as pl
import streamlit as st


def render_exposure_timeseries(df: pl.DataFrame) -> None:
    """Render factor exposure evolution over time.

    Args:
        df: Polars DataFrame with columns [date, factor, exposure].

    Returns:
        None. Renders a Plotly line chart in Streamlit.

    Example:
        >>> from datetime import date
        >>> data = pl.DataFrame({
        ...     "date": [date(2024, 1, 1)],
        ...     "factor": ["momentum_12_1"],
        ...     "exposure": [0.25],
        ... })
        >>> render_exposure_timeseries(data)
    """

    if df is None or df.is_empty():
        st.info("No time-series exposure data available.")
        return

    required = {"date", "factor", "exposure"}
    missing = required.difference(df.columns)
    if missing:
        st.error(f"Missing required columns: {sorted(missing)}")
        return

    sorted_df = df.sort(["date", "factor"])
    factors = sorted_df.select("factor").unique().to_series().to_list()

    fig = go.Figure()

    for factor in factors:
        subset = sorted_df.filter(pl.col("factor") == factor)
        fig.add_trace(
            go.Scatter(
                x=subset["date"].to_list(),
                y=subset["exposure"].to_list(),
                mode="lines",
                name=factor,
                hovertemplate="%{x}<br>%{y:.3f}<extra></extra>",
            )
        )

    fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.6)

    fig.update_layout(
        title="Factor Exposure Over Time",
        xaxis_title="Date",
        yaxis_title="Exposure",
        height=350,
        legend={"orientation": "h", "y": -0.25},
        margin={"l": 60, "r": 40, "t": 60, "b": 60},
    )

    st.plotly_chart(fig, use_container_width=True)


__all__ = ["render_exposure_timeseries"]
