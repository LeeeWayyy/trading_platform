"""Interactive factor exposure heatmap component."""

from __future__ import annotations

import plotly.graph_objects as go
import polars as pl
import streamlit as st


def render_heatmap(df: pl.DataFrame) -> None:
    """Render a factor exposure heatmap.

    Args:
        df: Polars DataFrame with columns [date, factor, exposure].

    Returns:
        None. Renders a Plotly heatmap in Streamlit.

    Example:
        >>> from datetime import date
        >>> data = pl.DataFrame({
        ...     "date": [date(2024, 1, 1)],
        ...     "factor": ["momentum_12_1"],
        ...     "exposure": [0.5],
        ... })
        >>> render_heatmap(data)
    """

    if df.is_empty():
        st.info("No exposure data available.")
        return

    required = {"date", "factor", "exposure"}
    missing = required.difference(df.columns)
    if missing:
        st.error(f"Missing required columns: {sorted(missing)}")
        return

    # Sort by date to keep columns ordered in the heatmap.
    sorted_df = df.sort("date")

    pivot = sorted_df.pivot(
        index="factor",
        on="date",
        values="exposure",
    )

    factors = pivot["factor"].to_list()
    date_columns = [col for col in pivot.columns if col != "factor"]
    z_values = pivot.drop("factor").to_numpy()

    fig = go.Figure(
        data=go.Heatmap(
            z=z_values,
            x=[str(d) for d in date_columns],
            y=factors,
            colorscale=[
                [0.0, "#d73027"],
                [0.5, "#ffffff"],
                [1.0, "#1a9850"],
            ],
            zmid=0,
            colorbar={"title": "Exposure"},
            hovertemplate=(
                "Factor: %{y}<br>Date: %{x}<br>Exposure: %{z:.3f}<extra></extra>"
            ),
        )
    )

    fig.update_layout(
        title="Factor Exposure Heatmap",
        xaxis_title="Date",
        yaxis_title="Factor",
        height=450,
        margin={"l": 80, "r": 40, "t": 60, "b": 40},
    )

    st.plotly_chart(fig, use_container_width=True)


__all__ = ["render_heatmap"]
