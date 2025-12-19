"""IC (Information Coefficient) time series visualization component.

Renders IC and Rank IC over time with rolling mean overlay.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import plotly.graph_objects as go
import streamlit as st

if TYPE_CHECKING:
    import polars as pl

# Minimum samples required for rolling calculations
MIN_ROLLING_SAMPLES = 5


def render_ic_timeseries(
    daily_ic: pl.DataFrame,
    title: str = "Information Coefficient Time Series",
    height: int = 400,
    rolling_window: int = 21,
) -> None:
    """Render IC time series chart.

    Args:
        daily_ic: DataFrame with columns: date, ic, rank_ic (optional)
        title: Chart title
        height: Chart height in pixels
        rolling_window: Window size for rolling mean (default 21 = ~1 month)

    Shows:
    - Raw IC values (light line)
    - Rolling mean IC (bold line)
    - Rank IC if available (separate traces)
    """

    if daily_ic is None or daily_ic.height == 0:
        st.info("No IC data available")
        return

    # Validate required columns
    if "date" not in daily_ic.columns or "ic" not in daily_ic.columns:
        st.error("Missing required columns: date, ic")
        return

    try:
        # Sort by date
        sorted_df = daily_ic.sort("date")

        # Compute rolling mean for IC
        min_samples = min(MIN_ROLLING_SAMPLES, rolling_window)
        rolling_ic = (
            sorted_df["ic"]
            .rolling_mean(window_size=rolling_window, min_samples=min_samples)
        )

        # Add rolling column
        chart_df = sorted_df.with_columns(rolling_ic.alias("rolling_ic"))

        # Check for rank_ic column
        has_rank_ic = "rank_ic" in sorted_df.columns

        if has_rank_ic:
            rolling_rank_ic = (
                sorted_df["rank_ic"]
                .rolling_mean(window_size=rolling_window, min_samples=min_samples)
            )
            chart_df = chart_df.with_columns(rolling_rank_ic.alias("rolling_rank_ic"))

        # Convert to pandas for plotly
        chart_pd = chart_df.to_pandas()

        # Create plotly figure
        fig = go.Figure()

        # Raw IC (light)
        fig.add_trace(
            go.Scatter(
                x=chart_pd["date"],
                y=chart_pd["ic"],
                mode="lines",
                name="IC",
                line={"color": "rgba(31, 119, 180, 0.3)", "width": 1},
                hovertemplate="%{x}<br>IC: %{y:.4f}<extra></extra>",
            )
        )

        # Rolling IC (bold)
        fig.add_trace(
            go.Scatter(
                x=chart_pd["date"],
                y=chart_pd["rolling_ic"],
                mode="lines",
                name=f"IC ({rolling_window}d MA)",
                line={"color": "#1f77b4", "width": 2.5},
                hovertemplate=f"%{{x}}<br>{rolling_window}d IC: %{{y:.4f}}<extra></extra>",
            )
        )

        if has_rank_ic:
            # Raw Rank IC (light)
            fig.add_trace(
                go.Scatter(
                    x=chart_pd["date"],
                    y=chart_pd["rank_ic"],
                    mode="lines",
                    name="Rank IC",
                    line={"color": "rgba(255, 127, 14, 0.3)", "width": 1},
                    hovertemplate="%{x}<br>Rank IC: %{y:.4f}<extra></extra>",
                )
            )

            # Rolling Rank IC (bold)
            fig.add_trace(
                go.Scatter(
                    x=chart_pd["date"],
                    y=chart_pd["rolling_rank_ic"],
                    mode="lines",
                    name=f"Rank IC ({rolling_window}d MA)",
                    line={"color": "#ff7f0e", "width": 2.5},
                    hovertemplate=(
                        f"%{{x}}<br>{rolling_window}d Rank IC: %{{y:.4f}}<extra></extra>"
                    ),
                )
            )

        # Add zero line
        fig.add_hline(y=0, line_dash="dash", line_color="gray", opacity=0.5)

        # Calculate mean IC for annotation (guard against empty/NaN series)
        if not chart_pd.empty and not chart_pd["ic"].isnull().all():
            mean_ic = chart_pd["ic"].mean()
            fig.add_hline(
                y=mean_ic,
                line_dash="dot",
                line_color="#1f77b4",
                opacity=0.7,
                annotation_text=f"Mean IC: {mean_ic:.4f}",
                annotation_position="right",
            )

        fig.update_layout(
            title=title,
            xaxis_title="Date",
            yaxis_title="Information Coefficient",
            height=height,
            legend={"yanchor": "top", "y": 0.99, "xanchor": "left", "x": 0.01},
            hovermode="x unified",
            yaxis={"tickformat": ".3f"},
        )

        st.plotly_chart(fig, use_container_width=True)

    except Exception as e:
        st.error(f"Failed to render IC chart: {e}")


__all__ = ["render_ic_timeseries"]
