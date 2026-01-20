"""IC time-series visualization component for NiceGUI.

Renders Information Coefficient over time as a Plotly line chart.
Ported from apps/web_console/components/ic_chart.py (Streamlit).
"""

from __future__ import annotations

import logging

import plotly.graph_objects as go
import polars as pl
from nicegui import ui

logger = logging.getLogger(__name__)


def render_ic_chart(
    daily_ic: pl.DataFrame | None,
    title: str = "Information Coefficient Over Time",
    height: int = 400,
) -> None:
    """Render IC time-series chart with Pearson and Rank IC.

    Args:
        daily_ic: DataFrame with columns [date, ic, rank_ic, rolling_ic_20d]
            - Data source: AlphaExplorerService.get_ic_timeseries()
        title: Chart title
        height: Chart height in pixels
    """
    if daily_ic is None or daily_ic.is_empty():
        ui.label("No IC data available for this signal.").classes("text-gray-500 text-center p-4")
        return

    # Validate required columns
    required_cols = {"date", "ic", "rank_ic"}
    missing_cols = required_cols - set(daily_ic.columns)
    if missing_cols:
        ui.label(f"Data schema error: missing columns {missing_cols}").classes(
            "text-red-600 text-center p-4"
        )
        return

    try:
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
            title=title,
            xaxis_title="Date",
            yaxis_title="IC",
            legend={
                "orientation": "h",
                "yanchor": "bottom",
                "y": 1.02,
                "xanchor": "right",
                "x": 1,
            },
            height=height,
            hovermode="x unified",
        )

        ui.plotly(fig).classes("w-full")

    except (ValueError, KeyError, IndexError, TypeError) as e:
        logger.warning(
            "IC chart rendering failed - invalid data",
            extra={"chart": "ic_chart", "error": str(e), "error_type": type(e).__name__},
            exc_info=True,
        )
        ui.label("Chart unavailable - data error").classes("text-gray-500 text-center p-4")


__all__ = ["render_ic_chart"]
