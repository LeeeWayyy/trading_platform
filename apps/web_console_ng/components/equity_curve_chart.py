"""Equity curve visualization component for NiceGUI.

Renders cumulative returns over time as a Plotly line chart.
Ported from apps/web_console/components/equity_curve_chart.py (Streamlit).
"""

from __future__ import annotations

import logging

import plotly.graph_objects as go
import polars as pl
from nicegui import ui

logger = logging.getLogger(__name__)


def render_equity_curve(
    daily_returns: pl.DataFrame | None,
    title: str = "Equity Curve",
    height: int = 400,
) -> None:
    """Render equity curve chart from daily returns.

    Args:
        daily_returns: DataFrame with columns: date, return
            - Data source: StrategyScopedDataAccess.get_performance_returns()
            - Schema: {date: date, return: float (e.g. 0.01 = 1%)}
        title: Chart title
        height: Chart height in pixels

    The chart shows cumulative returns computed as:
    (1 + r1) * (1 + r2) * ... * (1 + rn) - 1
    """
    if daily_returns is None or daily_returns.height == 0:
        ui.label("No return data available for equity curve").classes(
            "text-gray-500 text-center p-4"
        )
        return

    # Validate required columns
    required_cols = {"date", "return"}
    missing_cols = required_cols - set(daily_returns.columns)
    if missing_cols:
        # Use inline label instead of ui.notify to avoid toast spam on re-renders
        ui.label(f"Data schema error: missing columns {missing_cols}").classes(
            "text-red-600 text-center p-4"
        )
        return

    try:
        # Sort by date and compute cumulative returns
        sorted_df = daily_returns.sort("date")

        # Filter out non-finite returns (NaN/inf can poison cumulative series)
        original_count = sorted_df.height
        sorted_df = sorted_df.filter(pl.col("return").is_finite())
        filtered_count = original_count - sorted_df.height
        if filtered_count > 0:
            ui.label(f"Warning: {filtered_count} invalid return value(s) excluded.").classes(
                "text-yellow-600 text-sm mb-2"
            )

        if sorted_df.height == 0:
            ui.label("No valid return data for equity curve.").classes(
                "text-gray-500 text-center p-4"
            )
            return

        cumulative = (1 + sorted_df["return"]).cum_prod() - 1
        chart_df = sorted_df.with_columns(cumulative.alias("cumulative_return"))
        chart_pd = chart_df.select(["date", "cumulative_return"]).to_pandas()

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

        # Add zero reference line
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

        ui.plotly(fig).classes("w-full")

    except (ValueError, KeyError, IndexError, TypeError, pl.exceptions.InvalidOperationError) as e:
        logger.warning(
            "Equity curve rendering failed - invalid data",
            extra={"chart": "equity_curve", "error": str(e), "error_type": type(e).__name__},
            exc_info=True,
        )
        ui.label("Chart unavailable - data error").classes("text-gray-500 text-center p-4")


__all__ = ["render_equity_curve"]
