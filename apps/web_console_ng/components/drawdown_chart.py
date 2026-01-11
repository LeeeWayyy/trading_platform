"""Drawdown visualization component for NiceGUI.

Renders maximum drawdown over time as a Plotly area chart.
Ported from apps/web_console/components/drawdown_chart.py (Streamlit).
"""

from __future__ import annotations

import logging

import plotly.graph_objects as go
import polars as pl
from nicegui import ui

logger = logging.getLogger(__name__)


def render_drawdown_chart(
    daily_returns: pl.DataFrame | None,
    title: str = "Drawdown",
    height: int = 300,
) -> None:
    """Render drawdown chart from daily returns.

    Args:
        daily_returns: DataFrame with columns: date, return
            - Data source: StrategyScopedDataAccess.get_performance_returns()
            - Schema: {date: date, return: float (e.g. 0.01 = 1%)}
        title: Chart title
        height: Chart height in pixels

    Drawdown is computed as:
    - cumulative = (1 + r).cumprod()
    - running_max = cumulative.cummax()
    - drawdown = (cumulative - running_max) / running_max
    """
    if daily_returns is None or daily_returns.height == 0:
        ui.label("No return data available for drawdown chart").classes(
            "text-gray-500 text-center p-4"
        )
        return

    # Validate required columns
    required_cols = {"date", "return"}
    missing_cols = required_cols - set(daily_returns.columns)
    if missing_cols:
        ui.notify(f"Missing columns: {missing_cols}", type="negative")
        return

    try:
        # Sort by date
        sorted_df = daily_returns.sort("date")

        # Filter out non-finite returns (NaN/inf can poison cumulative series)
        original_count = sorted_df.height
        sorted_df = sorted_df.filter(pl.col("return").is_finite())
        filtered_count = original_count - sorted_df.height
        if filtered_count > 0:
            ui.label(
                f"Warning: {filtered_count} invalid return value(s) excluded."
            ).classes("text-yellow-600 text-sm mb-2")

        if sorted_df.height == 0:
            ui.label("No valid return data for drawdown chart.").classes(
                "text-gray-500 text-center p-4"
            )
            return

        # Compute cumulative wealth (1 + cumulative return)
        cumulative = (1 + sorted_df["return"]).cum_prod()

        # Running maximum
        running_max = cumulative.cum_max()

        # Drawdown = (current - peak) / peak
        # LOW fix: Avoid division by zero when running_max is 0 (100% loss scenario)
        # If running_max is 0, drawdown is -1.0 (complete loss)
        drawdown = pl.when(running_max == 0).then(-1.0).otherwise(
            (cumulative - running_max) / running_max
        )

        # Add columns
        chart_df = sorted_df.with_columns(drawdown.alias("drawdown"))

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
            max_dd_idx = chart_pd["drawdown"].idxmin()
            max_dd_date = chart_pd.loc[max_dd_idx, "date"]

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

        ui.plotly(fig).classes("w-full")

    except (ValueError, KeyError, IndexError, TypeError) as e:
        logger.warning(
            "Drawdown chart rendering failed - invalid data",
            extra={"chart": "drawdown", "error": str(e), "error_type": type(e).__name__},
            exc_info=True,
        )
        ui.label("Chart unavailable - data error").classes("text-gray-500 text-center p-4")


__all__ = ["render_drawdown_chart"]
